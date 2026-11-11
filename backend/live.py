"""Blink live view -> fragmented MP4 -> MediaSource over WebSocket.

The Blink relay speaks its proprietary IMMIS-over-TLS protocol and hands us a
raw MPEG-TS stream.  blinkpy already implements that relay; this module only
remuxes the TS to fMP4 (no re-encoding) and fans the bytes out to TV clients.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any

from .mp4_codec import FragmentScanner, describe_init, moov_end

LOGGER = logging.getLogger(__name__)

READ_SIZE = 64 * 1024
INIT_MAX_BYTES = 8 * 1024 * 1024
FIRST_FRAME_TIMEOUT = 32.0
START_TIMEOUT = 45.0
STOP_TIMEOUT = 8.0
IDLE_STOP_SECONDS = 10.0
MAX_SPLIT_SESSIONS = 4


@dataclass(eq=False)
class LiveClient:
    ws: Any
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=180))
    pump: asyncio.Task | None = None
    # False until a late joiner reaches the next fragment boundary.
    synced: bool = True


class LiveSession:
    """One Blink liveview, shared by every connected WebSocket client."""

    def __init__(
        self,
        camera: Any,
        manager: "LiveManager",
        audio_track: int = 0,
        max_seconds: int = 285,
    ) -> None:
        self.camera = camera
        self.manager = manager
        self.audio_track = 1 if audio_track else 0
        self.max_seconds = max_seconds
        self.stream = None
        self.ffmpeg = None
        self.clients: set[LiveClient] = set()
        self.finished = False
        self.ready_info: dict[str, Any] | None = None
        self.init_bytes = b""
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None
        self._feed_task: asyncio.Task | None = None
        self._watchdog: asyncio.Task | None = None
        self._idle_task: asyncio.Task | None = None
        self._liveness_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stop_lock = asyncio.Lock()
        self._stopping = False
        self._done = asyncio.Event()
        self._stderr_tail: list[str] = []
        self._last_client_seen: float | None = None
        self._last_data_sent: float | None = None

    # ------------------------------------------------------------------ setup
    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="blink-live-session")
        self._last_client_seen = time.monotonic()
        self._liveness_task = asyncio.create_task(
            self._liveness_loop(), name="blink-live-liveness"
        )

    async def add_client(self, ws: Any) -> LiveClient | None:
        if self.finished:
            with contextlib.suppress(Exception):
                await ws.send_json(
                    {
                        "type": "error",
                        "message": "This live session has ended.",
                        "retryable": True,
                    }
                )
            return None
        client = LiveClient(ws)
        self.clients.add(client)
        self._last_client_seen = time.monotonic()
        self._last_data_sent = time.monotonic()
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        # A reconnect can land after the init segment was already parsed.
        if self.ready_info and self.init_bytes:
            client.synced = False
            client.queue.put_nowait(("json", self.ready_info))
            client.queue.put_nowait(("bytes", self.init_bytes))
        client.pump = asyncio.create_task(
            self._client_pump(client), name="blink-live-client"
        )
        return client

    async def remove_client(self, client: LiveClient | None) -> None:
        if client is None:
            return
        self.clients.discard(client)
        self._last_client_seen = time.monotonic()
        if client.pump and not client.pump.done():
            client.pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await client.pump
        self._schedule_idle_stop()

    def _schedule_idle_stop(self) -> None:
        if self.finished or self._stopping or self.clients:
            return
        if self._idle_task and not self._idle_task.done():
            return
        self._idle_task = asyncio.create_task(
            self._stop_when_idle(), name="blink-live-idle"
        )

    async def _liveness_loop(self) -> None:
        """Stop a liveview even if the WebSocket close never reaches us."""

        try:
            while not self.finished:
                await asyncio.sleep(3.0)
                for client in list(self.clients):
                    if client.ws.closed:
                        self.clients.discard(client)
                if self.clients:
                    self._last_client_seen = time.monotonic()
                    if (
                        self.ready_info is not None
                        and self._last_data_sent is not None
                        and time.monotonic() - self._last_data_sent > 25.0
                    ):
                        await self.stop("The TV stopped consuming the live stream.")
                        return
                    continue
                if self._last_client_seen is None:
                    self._last_client_seen = time.monotonic()
                    continue
                if time.monotonic() - self._last_client_seen >= IDLE_STOP_SECONDS:
                    await self.stop("No viewers connected.")
                    return
        except asyncio.CancelledError:
            pass

    async def _stop_when_idle(self) -> None:
        try:
            await asyncio.sleep(IDLE_STOP_SECONDS)
            if not self.clients:
                await self.stop("No viewers connected.")
        except asyncio.CancelledError:
            pass

    # --------------------------------------------------------------- pipeline
    async def _run(self) -> None:
        try:
            await self._broadcast_json(
                {
                    "type": "status",
                    "state": "waking",
                    "message": "Waking the camera and opening the Blink relay…",
                }
            )
            LOGGER.info("Opening live view for %s", self.camera.name)
            try:
                stream = await asyncio.wait_for(
                    self.camera.init_livestream(), timeout=START_TIMEOUT
                )
            except NotImplementedError as exc:
                raise RuntimeError(
                    "This camera does not expose an IMMIS live stream."
                ) from exc
            except asyncio.TimeoutError as exc:
                raise RuntimeError(
                    "Blink did not return a live-view relay in time."
                ) from exc

            self.stream = stream
            await stream.start("127.0.0.1", 0)
            self._feed_task = asyncio.create_task(
                stream.feed(), name="blink-live-relay"
            )
            LOGGER.info("Blink relay listening for %s", self.camera.name)

            self._watchdog = asyncio.create_task(
                self._watchdog_task(), name="blink-live-watchdog"
            )
            await self._pump_ffmpeg(stream.url)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface any relay/ffmpeg error
            self.last_error = _short_error(exc)
            LOGGER.warning("Live view failed for %s: %s", self.camera.name, exc)
            await self._broadcast_json(
                {
                    "type": "error",
                    "message": self.last_error,
                    "retryable": True,
                }
            )
        finally:
            if self.last_error:
                # The error frame was already sent.  Closing the session now is
                # enough; an additional "ended" would make the TV race a
                # reconnect against a visible error.
                await self.stop(self.last_error, retryable=True, notify=False)
            else:
                await self.stop(
                    "Blink ended the live view. Reconnecting…", retryable=True
                )

    async def _watchdog_task(self) -> None:
        try:
            await asyncio.sleep(self.max_seconds)
            await self.stop(
                "Blink limits each live view to about five minutes. Reconnecting…",
                retryable=True,
            )
        except asyncio.CancelledError:
            pass

    async def _pump_ffmpeg(self, relay_url: str) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is not installed in the bridge container.")

        flags = "frag_keyframe+empty_moov+default_base_moof"
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
            relay_url,
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
            args += [
                "-map",
                f"0:a:{self.audio_track}?",
                "-c:a",
                "copy",
                "-bsf:a",
                "aac_adtstoasc",
            ]
            # AAC in MPEG-TS only gains its AudioSpecificConfig after the first
            # packet.  delay_moov waits for that, so the MP4 esds is usable.
            flags += "+delay_moov"
        else:
            args += ["-an"]
        args += [
            "-max_muxing_queue_size",
            "2048",
            "-f",
            "mp4",
            "-movflags",
            flags,
            "-",
        ]

        LOGGER.info(
            "Starting ffmpeg remux for %s (audio=%s track=%s)",
            self.camera.name,
            audio_enabled,
            self.audio_track if audio_enabled else "-",
        )
        self.ffmpeg = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self.ffmpeg.stdout is not None
        assert self.ffmpeg.stderr is not None
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(self.ffmpeg.stderr), name="blink-live-ffmpeg-log"
        )

        init, info = await asyncio.wait_for(
            self._read_init(self.ffmpeg.stdout), timeout=FIRST_FRAME_TIMEOUT
        )
        if info is None:
            tail = " | ".join(self._stderr_tail[-6:])
            raise RuntimeError(
                "The camera woke up but did not produce a video keyframe."
                + (f" ffmpeg: {tail}" if tail else "")
            )

        # The first read can run past the moov box. Late joiners must receive
        # only the init segment, then start at a whole fragment.
        self.init_bytes = init[: moov_end(init)]
        scanner = FragmentScanner()
        scanner.feed(init)
        self.ready_info = {
            "type": "ready",
            "video_codec": info["video_codec"],
            "audio_codec": info["audio_codec"],
            "audio_track": self.audio_track,
            "camera": self.camera.name,
        }
        LOGGER.info(
            "Live stream ready for %s: %s / %s",
            self.camera.name,
            info["video_codec"],
            info["audio_codec"] or "no audio",
        )
        await self._broadcast_json(self.ready_info)
        self._broadcast_bytes(init)

        while True:
            chunk = await self.ffmpeg.stdout.read(READ_SIZE)
            if not chunk:
                break
            self._broadcast_bytes(chunk, scanner.feed(chunk))

        returncode = await self.ffmpeg.wait()
        tail = " | ".join(self._stderr_tail[-4:])
        if returncode not in (0, -15):
            raise RuntimeError(
                "The live remuxer stopped unexpectedly."
                + (f" ffmpeg: {tail}" if tail else "")
            )

    async def _read_init(self, reader: asyncio.StreamReader) -> tuple[bytes, dict | None]:
        buffer = b""
        while len(buffer) < INIT_MAX_BYTES:
            chunk = await reader.read(READ_SIZE)
            if not chunk:
                return buffer, None
            buffer += chunk
            info = describe_init(buffer)
            if info is not None:
                return buffer, info
        return buffer, None

    async def _drain_stderr(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    return
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                self._stderr_tail.append(line)
                self._stderr_tail = self._stderr_tail[-30:]
                LOGGER.debug("ffmpeg: %s", line)
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - diagnostics must not kill the stream
            LOGGER.debug("ffmpeg stderr reader stopped", exc_info=True)

    # ----------------------------------------------------------------- output
    def _broadcast_bytes(self, data: bytes, fragment_at: int | None = None) -> None:
        if self._stopping:
            return
        for client in list(self.clients):
            if client.synced:
                self._offer(client, "bytes", data)
            elif fragment_at is not None:
                client.synced = True
                self._offer(client, "bytes", data[fragment_at:])

    async def _broadcast_json(self, payload: dict[str, Any]) -> None:
        if self._stopping:
            return
        for client in list(self.clients):
            self._offer(client, "json", payload)

    def _offer(self, client: LiveClient, kind: str, payload: Any) -> None:
        if client.ws.closed:
            return
        try:
            client.queue.put_nowait((kind, payload))
        except asyncio.QueueFull:
            LOGGER.warning("Live client is too slow; disconnecting it")
            asyncio.create_task(self._close_client(client))

    async def _client_pump(self, client: LiveClient) -> None:
        try:
            while True:
                kind, payload = await client.queue.get()
                if client.ws.closed:
                    return
                if kind == "json":
                    await client.ws.send_json(payload)
                else:
                    await client.ws.send_bytes(payload)
                self._last_data_sent = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a TV may vanish at any moment
            LOGGER.debug("Live client disconnected", exc_info=True)
        finally:
            self.clients.discard(client)
            self._last_client_seen = time.monotonic()
            self._schedule_idle_stop()

    async def _close_client(self, client: LiveClient) -> None:
        self.clients.discard(client)
        if client.pump and not client.pump.done():
            client.pump.cancel()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                client.ws.close(code=1013, message=b"slow client"), timeout=1.0
            )

    # ------------------------------------------------------------------- stop
    async def stop(self, reason: str, retryable: bool = False, notify: bool = True) -> None:
        async with self._stop_lock:
            if self._stopping:
                if asyncio.current_task() is self._task:
                    return
                # A second caller must never wait forever for a dead WebSocket.
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._done.wait(), timeout=12.0)
                return
            self._stopping = True

        try:
            await self._cleanup(reason, retryable, notify)
        finally:
            # Always release waiters, even if a TV socket or ffmpeg pipe wedged.
            self.finished = True
            self.manager.forget(self)
            self._done.set()
            LOGGER.info("Live session stopped for %s: %s", self.camera.name, reason)

    async def _cleanup(self, reason: str, retryable: bool, notify: bool) -> None:
        current = asyncio.current_task()
        for task in (self._watchdog, self._idle_task, self._liveness_task):
            if task and not task.done() and task is not current:
                task.cancel()

        if self._task and not self._task.done() and self._task is not current:
            await self._cancel_wait(self._task, 5.0)
        if self._feed_task and not self._feed_task.done():
            # blinkpy's poll() sends command/done in its finally block.  If Blink
            # is slow, waiting for that network call would block the next camera
            # forever.  Bound the cleanup and let the relay task finish on its
            # own after its socket has been closed.
            await self._cancel_wait(self._feed_task, 2.0)

        if self.stream is not None:
            with contextlib.suppress(Exception):
                self.stream.stop()

        await self._terminate_ffmpeg()
        if self._stderr_task and not self._stderr_task.done():
            await self._cancel_wait(self._stderr_task, 1.0)

        if notify:
            message = {
                "type": "ended",
                "message": reason,
                "retryable": retryable,
            }
            for client in list(self.clients):
                if not client.ws.closed:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(client.ws.send_json(message), timeout=1.0)
        for client in list(self.clients):
            self.clients.discard(client)
            if client.pump and not client.pump.done():
                client.pump.cancel()
            if not client.ws.closed:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        client.ws.close(code=1000 if not retryable else 1012),
                        timeout=1.0,
                    )

    async def _cancel_wait(self, task: asyncio.Task, timeout: float) -> None:
        if task.done():
            return
        task.cancel()
        try:
            # shield() keeps wait_for's timeout from cancelling the task a
            # second time; a task that ignores cancellation is left behind and
            # cannot hold the live-slot lock.
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass

    async def _terminate_ffmpeg(self) -> None:
        proc = self.ffmpeg
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=3.0)


class LiveManager:
    """Runs one exclusive liveview, or up to MAX_SPLIT_SESSIONS for split view."""

    def __init__(self, camera_lookup, max_seconds: int = 285) -> None:
        self._camera_lookup = camera_lookup
        self._max_seconds = max_seconds
        self._sessions: dict[str, LiveSession] = {}
        self._lock = asyncio.Lock()
        self.last_error: str | None = None

    async def get_or_create(
        self, camera_id: str, audio_track: int = 0, exclusive: bool = True
    ) -> LiveSession:
        async with self._lock:
            existing = self._sessions.get(camera_id)
            if (
                existing is not None
                and not existing.finished
                and not existing._stopping
                and existing.audio_track == (1 if audio_track else 0)
            ):
                return existing

            if exclusive:
                for session in list(self._sessions.values()):
                    await session.stop("Switched to another camera.", retryable=True)
                self._sessions.clear()
            else:
                stale = self._sessions.pop(camera_id, None)
                if stale is not None:
                    await stale.stop("Replaced by a new live view.", retryable=True)
                if len(self._sessions) >= MAX_SPLIT_SESSIONS:
                    raise RuntimeError("Too many cameras are already live.")

            camera = self._camera_lookup(camera_id)
            if camera is None:
                raise KeyError("Unknown camera")
            session = LiveSession(
                camera=camera,
                manager=self,
                audio_track=audio_track,
                max_seconds=self._max_seconds,
            )
            self._sessions[camera_id] = session
            session.start()
            return session

    def forget(self, session: LiveSession) -> None:
        for key, value in list(self._sessions.items()):
            if value is session:
                self._sessions.pop(key, None)
                self.last_error = session.last_error

    async def stop_camera(self, camera_id: str) -> bool:
        session = self._sessions.get(camera_id)
        if session is None:
            return False
        await session.stop("Stopped from the app.", retryable=True)
        return True

    async def close(self) -> None:
        for session in list(self._sessions.values()):
            await session.stop("Bridge shutting down.", retryable=True)
        self._sessions.clear()

    def active(self) -> dict[str, str]:
        return {
            key: ("running" if not session.finished else "ending")
            for key, session in self._sessions.items()
        }


def _short_error(exc: BaseException) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text[:500]
