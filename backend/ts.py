"""Low-latency raw MPEG-TS relay for Samsung AVPlay.

HLS is robust, but its live edge is one camera GOP behind (about four seconds
for Blink).  AVPlay can also consume a chunked MPEG-TS HTTP stream, so this
module exposes the relay's original TS directly.  The TV app tries this path
first and transparently falls back to HLS if the firmware rejects it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import urllib.parse
from typing import Any

from aiohttp import web

LOGGER = logging.getLogger(__name__)
IDLE_STOP_SECONDS = 30.0


class TsSession:
    def __init__(self, camera, manager: "TsManager", max_seconds: int):
        self.camera = camera
        self.manager = manager
        self.max_seconds = max_seconds
        self.id = secrets.token_urlsafe(18)
        self.stream = None
        self.feed_task: asyncio.Task | None = None
        self.reader_task: asyncio.Task | None = None
        self.liveness_task: asyncio.Task | None = None
        self.watchdog_task: asyncio.Task | None = None
        self.clients: set[asyncio.Queue] = set()
        self.finished = False
        self.last_error: str | None = None
        self.last_touch = 0.0
        self._stopping = False

    @property
    def stream_url(self) -> str:
        return f"/api/ts/{self.id}/stream.ts"

    def touch(self) -> None:
        self.last_touch = asyncio.get_running_loop().time()

    async def start(self) -> None:
        try:
            self.stream = await asyncio.wait_for(
                self.camera.init_livestream(), timeout=45.0
            )
            await self.stream.start("127.0.0.1", 0)
            self.feed_task = asyncio.create_task(
                self.stream.feed(), name="blink-ts-relay"
            )
            self.last_touch = asyncio.get_running_loop().time()
            self.liveness_task = asyncio.create_task(
                self._liveness_loop(), name="blink-ts-liveness"
            )
            self.watchdog_task = asyncio.create_task(
                self._watchdog(), name="blink-ts-watchdog"
            )
            LOGGER.info("Raw TS live ready for %s in %s", self.camera.name, self.id)
        except Exception:
            await self.stop("TS start failed.")
            raise

    async def attach(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=192)
        self.clients.add(queue)
        self.touch()
        if self.reader_task is None or self.reader_task.done():
            self.reader_task = asyncio.create_task(
                self._read_relay(), name="blink-ts-reader"
            )
        return queue

    def detach(self, queue: asyncio.Queue) -> None:
        self.clients.discard(queue)
        self.touch()

    async def _read_relay(self) -> None:
        if self.stream is None:
            return
        parsed = urllib.parse.urlparse(self.stream.url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port
        if not port:
            self.last_error = "The Blink relay did not expose a TCP port."
            await self.stop(self.last_error)
            return
        writer = None
        try:
            reader, writer = await asyncio.open_connection(host, port)
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                if not self.clients:
                    continue
                self.touch()
                dead = []
                for queue in list(self.clients):
                    try:
                        queue.put_nowait(chunk)
                    except asyncio.QueueFull:
                        dead.append(queue)
                for queue in dead:
                    self.clients.discard(queue)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc) or exc.__class__.__name__
            LOGGER.warning("TS relay read failed for %s: %s", self.camera.name, exc)
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
            if not self._stopping and not self.finished:
                await self.stop("The raw TS relay ended.")

    async def _liveness_loop(self) -> None:
        try:
            while not self.finished:
                await asyncio.sleep(5.0)
                now = asyncio.get_running_loop().time()
                if now - self.last_touch > IDLE_STOP_SECONDS:
                    await self.stop("No raw TS client.")
                    return
        except asyncio.CancelledError:
            pass

    async def _watchdog(self) -> None:
        try:
            await asyncio.sleep(self.max_seconds)
            await self.stop("Blink's live-view limit was reached.")
        except asyncio.CancelledError:
            pass

    async def serve(self, request: web.Request) -> web.StreamResponse:
        if self.finished:
            raise web.HTTPNotFound()
        queue = await self.attach()
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "video/mp2t",
                "Cache-Control": "no-store",
                "Connection": "keep-alive",
            },
        )
        response.enable_chunked_encoding()
        await response.prepare(request)
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                await response.write(chunk)
                self.touch()
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("TS HTTP client ended for %s: %s", self.camera.name, exc)
        finally:
            self.detach(queue)
            with contextlib.suppress(Exception):
                await response.write_eof()
        return response

    async def stop(self, reason: str = "Stopped.") -> None:
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
        current = asyncio.current_task()
        if self.reader_task and not self.reader_task.done() and self.reader_task is not current:
            self.reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(self.reader_task), timeout=2.0)
        if self.stream is not None:
            with contextlib.suppress(Exception):
                self.stream.stop()
        for queue in list(self.clients):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)
        self.clients.clear()
        self.finished = True
        self.manager.forget(self)
        LOGGER.info("Raw TS live stopped for %s: %s", self.camera.name, reason)


class TsManager:
    def __init__(self, camera_lookup, max_seconds: int = 285):
        self._camera_lookup = camera_lookup
        self._max_seconds = max_seconds
        self._sessions: dict[str, TsSession] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, camera_id: str) -> TsSession:
        async with self._lock:
            existing = self._sessions.get(camera_id)
            if existing is not None and not existing.finished and not existing.last_error:
                return existing
            for session in list(self._sessions.values()):
                await session.stop("Switched to another camera.")
            self._sessions.clear()
            camera = self._camera_lookup(camera_id)
            if camera is None:
                raise KeyError("Unknown camera")
            session = TsSession(camera, self, self._max_seconds)
            self._sessions[session.id] = session
            try:
                await session.start()
            except Exception:
                self._sessions.pop(session.id, None)
                raise
            return session

    def get(self, session_id: str) -> TsSession | None:
        session = self._sessions.get(session_id)
        if session is None or session.finished:
            return None
        return session

    def forget(self, session: TsSession) -> None:
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
        return {"ts:" + key: "running" for key in self._sessions}
