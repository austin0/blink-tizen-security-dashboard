"""Shutdown paths must not deadlock the bridge."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.live import LiveSession
from tests.test_live_pipeline import FakeCamera, FakeManager, FakeRelay, FakeWebSocket


class LiveStopTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not shutil.which("ffmpeg"):
            raise unittest.SkipTest("ffmpeg is required")
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.ts = cls.root / "sample.ts"
        import subprocess

        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=2",
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "libx264", "-preset", "ultrafast", "-g", "15",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "32k", "-ac", "1",
                "-f", "mpegts", str(cls.ts),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    async def test_watchdog_stop_completes_without_deadlock(self) -> None:
        relay = FakeRelay(self.ts)
        session = LiveSession(FakeCamera(relay), FakeManager(), max_seconds=0.2)
        ws = FakeWebSocket()
        await session.add_client(ws)
        with patch.dict(os.environ, {"BLINK_AUDIO": "1"}):
            session.start()
            await asyncio.wait_for(session._done.wait(), timeout=12)
        self.assertTrue(session.finished)
        self.assertTrue(ws.closed)
        await session.stop("again")  # idempotent and immediate

    async def test_stop_does_not_hang_on_a_dead_websocket(self) -> None:
        class DeadWebSocket(FakeWebSocket):
            async def send_json(self, payload) -> None:
                await asyncio.sleep(3600)

            async def close(self, code=1000, message=b"") -> None:
                await asyncio.sleep(3600)

        session = LiveSession(
            FakeCamera(FakeRelay(self.ts)), FakeManager(), max_seconds=60
        )
        await session.add_client(DeadWebSocket())
        started = time.monotonic()
        await asyncio.wait_for(session.stop("TV disappeared"), timeout=6)
        self.assertLess(time.monotonic() - started, 6)
        self.assertTrue(session.finished)


if __name__ == "__main__":
    unittest.main()
