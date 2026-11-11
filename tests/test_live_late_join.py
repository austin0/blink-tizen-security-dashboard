"""A viewer joining a running session must start on a fragment boundary."""

from __future__ import annotations

import asyncio
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.live import LiveManager, LiveSession
from backend.mp4_codec import FragmentScanner, moov_end


class FakeWebSocket:
    closed = False

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send_json(self, payload) -> None:
        pass

    async def send_bytes(self, payload: bytes) -> None:
        self.sent.append(payload)


class FragmentScannerTests(unittest.TestCase):
    def test_finds_moof_after_a_box_spanning_chunks(self) -> None:
        mdat = (16).to_bytes(4, "big") + b"mdat" + b"x" * 8
        moof = (8).to_bytes(4, "big") + b"moof"
        stream = mdat + moof
        scanner = FragmentScanner()
        self.assertIsNone(scanner.feed(stream[:12]))
        self.assertEqual(scanner.feed(stream[12:]), 4)

    def test_header_split_across_chunks_is_not_reported(self) -> None:
        moof = (8).to_bytes(4, "big") + b"moof"
        scanner = FragmentScanner()
        self.assertIsNone(scanner.feed(moof[:3]))
        self.assertIsNone(scanner.feed(moof[3:]))


class ReconnectDuringStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_stopping_session_is_not_reused(self) -> None:
        manager = LiveManager(lambda camera_id: object())
        stopping = LiveSession(camera=None, manager=manager)
        stopping._stopping = True
        manager._sessions["1"] = stopping
        with (
            patch.object(LiveSession, "stop", AsyncMock()) as stop,
            patch.object(LiveSession, "start"),
        ):
            session = await manager.get_or_create("1", exclusive=False)
        stop.assert_awaited_once()
        self.assertIsNot(session, stopping)


class LateJoinDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_client_skips_bytes_before_next_fragment(self) -> None:
        session = LiveSession(camera=None, manager=None)
        session.ready_info = {"type": "ready"}
        session.init_bytes = b"INIT"
        ws = FakeWebSocket()
        client = await session.add_client(ws)
        session._broadcast_bytes(b"middle-of-fragment")
        session._broadcast_bytes(b"tailMOOF", 4)
        session._broadcast_bytes(b"next")
        for _ in range(5):
            await asyncio.sleep(0)
        client.pump.cancel()
        self.assertEqual(ws.sent, [b"INIT", b"MOOF", b"next"])


class RealStreamTests(unittest.TestCase):
    def test_every_late_join_point_decodes(self) -> None:
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg is required")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=6",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=6",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "32k", "-ac", "1",
                    "-f", "mp4", "-movflags",
                    "frag_keyframe+empty_moov+default_base_moof+delay_moov",
                    str(source),
                ],
                check=True,
            )
            data = source.read_bytes()
            init = data[: moov_end(data)]
            rng = random.Random(7)
            pos = 0
            chunks = []
            while pos < len(data):
                size = rng.randint(1, 9000)
                chunks.append(data[pos : pos + size])
                pos += size

            scanner = FragmentScanner()
            starts = [
                (index, offset)
                for index, chunk in enumerate(chunks)
                if (offset := scanner.feed(chunk)) is not None
            ]
            self.assertGreater(len(starts), 3)
            for index, offset in starts[1:]:
                self.assertEqual(chunks[index][offset + 4 : offset + 8], b"moof")
                late = root / "late.mp4"
                late.write_bytes(init + chunks[index][offset:] + b"".join(chunks[index + 1 :]))
                result = subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", str(late), "-f", "null", "-"],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
