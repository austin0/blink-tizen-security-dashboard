"""End-to-end remux test using a fake Blink IMMIS relay.

The fake relay serves a synthetic MPEG-TS file over TCP, exactly where blinkpy
would expose its local stream.  Everything downstream is the real pipeline:
ffmpeg, fMP4 fragmentation, codec discovery, WebSocket fan-out.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.live import LiveSession
from backend.mp4_codec import describe_init


class FakeWebSocket:
    def __init__(self) -> None:
        self.closed = False
        self.json: list[dict] = []
        self.data = bytearray()

    async def send_json(self, payload) -> None:
        self.json.append(payload)

    async def send_bytes(self, payload: bytes) -> None:
        self.data.extend(payload)

    async def close(self, code=1000, message=b"") -> None:
        self.closed = True


class FakeRelay:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.server = None
        self.writer = None
        self._feed_event = asyncio.Event()

    async def start(self, host="127.0.0.1", port=0) -> None:
        self.server = await asyncio.start_server(self._handle, host, port)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        data = self.source.read_bytes()
        try:
            for offset in range(0, len(data), 32768):
                writer.write(data[offset : offset + 32768])
                await writer.drain()
                await asyncio.sleep(0.001)
        finally:
            writer.close()
            self.writer = writer

    @property
    def url(self) -> str:
        host, port = self.server.sockets[0].getsockname()[:2]
        return f"tcp://{host}:{port}"

    async def feed(self) -> None:
        await self._feed_event.wait()

    def stop(self) -> None:
        self._feed_event.set()
        if self.server:
            self.server.close()


class FakeCamera:
    name = "Test Camera"
    camera_id = "42"

    def __init__(self, relay: FakeRelay) -> None:
        self.relay = relay

    async def init_livestream(self) -> FakeRelay:
        return self.relay


class FakeManager:
    def __init__(self) -> None:
        self.forgotten = None

    def forget(self, session) -> None:
        self.forgotten = session


class LivePipelineTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not shutil.which("ffmpeg"):
            raise unittest.SkipTest("ffmpeg is required")
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.ts = cls.root / "sample.ts"
        cls.video_ts = cls.root / "video.ts"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=3",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=3",
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "32k", "-ac", "1",
                "-f", "mpegts", str(cls.ts),
            ],
            check=True,
        )
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(cls.ts), "-map", "0:v:0", "-c", "copy",
                "-f", "mpegts", str(cls.video_ts),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    async def _run_session(self, source: Path, audio: bool) -> tuple[FakeWebSocket, LiveSession, FakeManager]:
        relay = FakeRelay(source)
        manager = FakeManager()
        session = LiveSession(FakeCamera(relay), manager, audio_track=0, max_seconds=60)
        ws = FakeWebSocket()
        await session.add_client(ws)
        with patch.dict(os.environ, {"BLINK_AUDIO": "1" if audio else "0"}):
            session.start()
            for _ in range(160):
                if any(item.get("type") == "ready" for item in ws.json) and len(ws.data) > 1000:
                    break
                await asyncio.sleep(0.05)
        return ws, session, manager

    async def test_audio_video_fmp4_is_broadcast(self) -> None:
        ws, session, manager = await self._run_session(self.ts, True)
        try:
            ready = [item for item in ws.json if item.get("type") == "ready"]
            self.assertTrue(ready, ws.json)
            self.assertEqual(ready[0]["audio_codec"], "mp4a.40.2")
            info = describe_init(bytes(ws.data))
            self.assertIsNotNone(info)
            self.assertIsNotNone(info["audio_codec"])
            self.assertTrue(str(info["video_codec"]).startswith("avc1."))
            self.assertGreater(len(ws.data), 5000)
        finally:
            await session.stop("test complete")
        self.assertIs(manager.forgotten, session)

    async def test_video_only_session(self) -> None:
        ws, session, manager = await self._run_session(self.video_ts, False)
        try:
            ready = [item for item in ws.json if item.get("type") == "ready"]
            self.assertTrue(ready, ws.json)
            self.assertIsNone(ready[0]["audio_codec"])
            info = describe_init(bytes(ws.data))
            self.assertIsNotNone(info)
            self.assertIsNone(info["audio_codec"])
        finally:
            await session.stop("test complete")


if __name__ == "__main__":
    unittest.main()
