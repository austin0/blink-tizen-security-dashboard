"""HLS live view for Tizen AVPlay.

MSE fails on some Tizen TV decoders even when the browser advertises support.
AVPlay is the TV's native pipeline, so the bridge also exposes a short-segment
HLS rendition (H.264/AAC copy, no re-encoding) that AVPlay can open directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path

from aiohttp import web

LOGGER = logging.getLogger(__name__)

FIRST_SEGMENT_TIMEOUT = 45.0
IDLE_STOP_SECONDS = 30.0
MAX_SEGMENT_FILES = 6
SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{16,64}\Z")
SEGMENT_RE = re.compile(r"seg\d{5}\.ts\Z")


class HlsSession:
    def __init__(self, camera, manager: "HlsManager", audio_track: int, max_seconds: int):
        self.camera = camera
        self.manager = manager
        self.audio_track = 1 if audio_track else 0
        self.max_seconds = max_seconds
        self.id = secrets.token_urlsafe(18)
        self.dir = Path(tempfile.mkdtemp(prefix="blink-hls-"))
        self.playlist = self.dir / "index.m3u8"
        self.stream = None
        self.ffmpeg = None
        self.feed_task: asyncio.Task | None = None
        self.monitor_task: asyncio.Task | None = None
        self.liveness_task: asyncio.Task | None = None
        self.watchdog_task: asyncio.Task | None = None
        self.finished = False
        self.last_error: str | None = None
        self.last_touch = time.monotonic()
        self._stop_lock = asyncio.Lock()
        self._stopping = False
        self._stderr_tail: list[str] = []

    @property
    def playlist_url(self) -> str:
        return f"/api/hls/{self.id}/index.m3u8"

    def touch(self) -> None:
        self.last_touch = time.monotonic()

    async def start(self) -> None:
        try:
            self.stream = await asyncio.wait_for(
                self.camera.init_livestream(), timeout=45.0
            )
            await self.stream.start("127.0.0.1", 0)
            self.feed_task = asyncio.create_task(
                self.stream.feed(), name="blink-hls-relay"
            )
            await self._start_ffmpeg()
            await self._wait_first_segment()
            self.touch()
            self.liveness_task = asyncio.create_task(
                self._liveness_loop(), name="blink-hls-liveness"
            )
            self.watchdog_task = asyncio.create_task(
                self._watchdog(), name="blink-hls-watchdog"
            )
            LOGGER.info("HLS live ready for %s in %s", self.camera.name, self.id)
        except Exception:
            await self.stop("HLS start failed.")
            raise

    async def _start_ffmpeg(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is not installed in the bridge container.")
        args = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-analyzeduration",
            "1500000",
            "-probesize",
            "1500000",
            "-i",
            self.stream.url,
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
        ]
        audio_enabled = os.environ.get("BLINK_AUDIO", "1").strip().lower() not in {
            "0",
            "false",
            "off",
            "no",
        }
        if audio_enabled:
            args += ["-map", f"0:a:{self.audio_track}?", "-c:a", "copy"]
        else:
            args += ["-an"]
        args += [
            "-max_muxing_queue_size",
            "2048",
            "-f",
            "hls",
            "-hls_time",
            "1",
            "-hls_list_size",
            str(MAX_SEGMENT_FILES),
            "-hls_flags",
            "delete_segments+independent_segments+omit_endlist+temp_file",
            "-hls_segment_filename",
            str(self.dir / "seg%05d.ts"),
            str(self.playlist),
        ]
        self.ffmpeg = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self.ffmpeg.stderr is not None
        asyncio.create_task(self._drain_stderr(self.ffmpeg.stderr))
        self.monitor_task = asyncio.create_task(
            self._monitor(), name="blink-hls-ffmpeg-monitor"
        )

    async def _drain_stderr(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                text = line.decode("utf-8", "replace").strip()
                if text:
                    self._stderr_tail.append(text)
                    self._stderr_tail = self._stderr_tail[-20:]
                    LOGGER.debug("hls ffmpeg: %s", text)
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            LOGGER.debug("hls stderr stopped", exc_info=True)

    async def _monitor(self) -> None:
        assert self.ffmpeg is not None
        code = await self.ffmpeg.wait()
        if self._stopping:
            return
        self.last_error = (
            "The HLS remuxer stopped."
            + (" " + " | ".join(self._stderr_tail[-4:]) if self._stderr_tail else "")
        )
        LOGGER.warning("HLS ffmpeg exited for %s: %s", self.camera.name, code)
        if not self._stopping and not self.finished:
            await self.stop("The HLS stream ended.")

    async def _wait_first_segment(self) -> None:
        deadline = time.monotonic() + FIRST_SEGMENT_TIMEOUT
        while time.monotonic() < deadline:
            if self.last_error:
                raise RuntimeError(self.last_error)
            if self.playlist.is_file() and self.playlist.stat().st_size > 0:
                segments = list(self.dir.glob("seg*.ts"))
                if segments and all(s.stat().st_size > 0 for s in segments):
                    text = self.playlist.read_text(encoding="utf-8", errors="replace")
                    if "#EXTINF" in text:
                        return
            await asyncio.sleep(0.25)
        if self.last_error:
            raise RuntimeError(self.last_error)
        raise RuntimeError("The camera woke up but did not produce an HLS segment.")

    async def _liveness_loop(self) -> None:
        try:
            while not self.finished:
                await asyncio.sleep(5.0)
                if time.monotonic() - self.last_touch > IDLE_STOP_SECONDS:
                    await self.stop("No HLS client requests.")
                    return
        except asyncio.CancelledError:
            pass

    async def _watchdog(self) -> None:
        try:
            await asyncio.sleep(self.max_seconds)
            await self.stop("Blink's live-view limit was reached.")
        except asyncio.CancelledError:
            pass

    async def stop(self, reason: str) -> None:
        async with self._stop_lock:
            if self._stopping:
                return
            self._stopping = True
        for task in (self.liveness_task, self.watchdog_task):
            if task and not task.done():
                task.cancel()
        if self.feed_task and not self.feed_task.done():
            self.feed_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(self.feed_task), timeout=2.0)
        if self.stream is not None:
            with contextlib.suppress(Exception):
                self.stream.stop()
        proc = self.ffmpeg
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
        shutil.rmtree(self.dir, ignore_errors=True)
        self.finished = True
        self.manager.forget(self)
        LOGGER.info("HLS live stopped for %s: %s", self.camera.name, reason)


class HlsManager:
    def __init__(self, camera_lookup, max_seconds: int = 285):
        self._camera_lookup = camera_lookup
        self._max_seconds = max_seconds
        self._sessions: dict[str, HlsSession] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, camera_id: str, audio_track: int = 0) -> HlsSession:
        async with self._lock:
            existing = self._sessions.get(camera_id)
            if (
                existing is not None
                and not existing.finished
                and existing.last_error is None
                and existing.audio_track == (1 if audio_track else 0)
            ):
                existing.touch()
                return existing
            for session in list(self._sessions.values()):
                await session.stop("Switched to another camera.")
            self._sessions.clear()
            camera = self._camera_lookup(camera_id)
            if camera is None:
                raise KeyError("Unknown camera")
            session = HlsSession(
                camera, self, 1 if audio_track else 0, self._max_seconds
            )
            self._sessions[session.id] = session
            try:
                await session.start()
            except Exception:
                self._sessions.pop(session.id, None)
                raise
            return session

    def get(self, session_id: str) -> HlsSession | None:
        session = self._sessions.get(session_id)
        if session is None or session.finished:
            return None
        return session

    def forget(self, session: HlsSession) -> None:
        self._sessions.pop(session.id, None)

    async def stop_camera(self, camera_id: str) -> bool:
        for session in list(self._sessions.values()):
            if str(session.camera.camera_id) == str(camera_id):
                await session.stop("Stopped from the app.")
                return True
        return False

    async def close(self) -> None:
        for session in list(self._sessions.values()):
            await session.stop("Bridge shutting down.")
        self._sessions.clear()

    def active(self) -> dict[str, str]:
        return {"hls:" + key: "running" for key in self._sessions}


async def serve_hls(request: web.Request, session: HlsSession) -> web.StreamResponse:
    filename = request.match_info["filename"]
    if filename != "index.m3u8" and not SEGMENT_RE.fullmatch(filename):
        raise web.HTTPNotFound()
    path = (session.dir / filename).resolve()
    if session.dir.resolve() not in path.parents or not path.is_file():
        raise web.HTTPNotFound()
    session.touch()
    if filename.endswith(".m3u8"):
        return web.Response(
            body=path.read_bytes(),
            content_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    return web.Response(
        body=path.read_bytes(),
        content_type="video/mp2t",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )
