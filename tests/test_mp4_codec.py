"""Codec discovery for the live fMP4 remux."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from backend.mp4_codec import describe_init


class Mp4CodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not shutil.which("ffmpeg"):
            raise unittest.SkipTest("ffmpeg is required")
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.ts = cls.root / "sample.ts"
        cls.mp4 = cls.root / "sample.mp4"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=2",
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
                "-i", str(cls.ts), "-map", "0:v:0", "-c:v", "copy",
                "-map", "0:a:0?", "-c:a", "copy", "-bsf:a", "aac_adtstoasc",
                "-f", "mp4",
                "-movflags", "frag_keyframe+empty_moov+default_base_moof+delay_moov",
                str(cls.mp4),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_reports_h264_and_aac(self) -> None:
        data = self.mp4.read_bytes()
        info = describe_init(data)
        self.assertIsNotNone(info)
        self.assertTrue(str(info["video_codec"]).startswith("avc1."))
        self.assertEqual(info["audio_codec"], "mp4a.40.2")
        self.assertGreater(info["moov_end"], 0)

    def test_incomplete_moov_is_not_reported(self) -> None:
        data = self.mp4.read_bytes()
        info = describe_init(data)
        self.assertIsNotNone(info)
        self.assertIsNone(describe_init(data[: info["moov_end"] - 1]))
        self.assertIsNone(describe_init(b""))


if __name__ == "__main__":
    unittest.main()
