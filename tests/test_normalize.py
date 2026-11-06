"""Tests de bout en bout (nécessitent ffmpeg). Lancer : python -m unittest"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from normalize_video.cli import main
from normalize_video.core import Settings, find_videos, measure, normalize

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def make_video(path: Path, audio: str | None, seconds: int = 4) -> Path:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
           "-i", "testsrc=size=160x120:rate=15"]
    if audio:
        cmd += ["-f", "lavfi", "-i", audio, "-c:a", "aac", "-shortest"]
    cmd += ["-t", str(seconds), "-c:v", "libx264", str(path)]
    subprocess.run(cmd, check=True)
    return path


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg requis")
class NormalizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.quiet = make_video(cls.tmp / "quiet.mp4", "sine=f=440,volume=0.03")
        cls.loud = make_video(cls.tmp / "loud.mkv", "anoisesrc=a=0.5")
        cls.mute = make_video(cls.tmp / "mute.mp4", None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_same_level_after_normalization(self):
        settings = Settings(target_i=-16.0)
        levels = []
        for src in (self.quiet, self.loud):
            out = self.tmp / "out" / src.name
            res = normalize(src, out, settings)
            self.assertTrue(res.ok, res.error)
            levels.append(measure(out).integrated)
        for lvl in levels:
            self.assertAlmostEqual(lvl, -16.0, delta=1.0)
        self.assertLess(abs(levels[0] - levels[1]), 1.0)

    def test_video_without_audio_is_skipped(self):
        res = normalize(self.mute, self.tmp / "mute_out.mp4")
        self.assertTrue(res.skipped)
        self.assertIsNone(res.error)

    def test_existing_output_not_overwritten(self):
        out = self.tmp / "exists.mp4"
        out.write_bytes(b"x")
        res = normalize(self.quiet, out, Settings(overwrite=False))
        self.assertTrue(res.skipped)
        self.assertEqual(out.read_bytes(), b"x")

    def test_refuses_to_overwrite_source(self):
        res = normalize(self.quiet, self.quiet, Settings(overwrite=True))
        self.assertIsNotNone(res.error)

    def test_find_videos_in_folder(self):
        names = {p.name for p in find_videos([self.tmp])}
        self.assertTrue({"quiet.mp4", "loud.mkv", "mute.mp4"} <= names)

    def test_cli(self):
        out = self.tmp / "cli_out"
        code = main([str(self.quiet), "-o", str(out), "--preset", "youtube"])
        self.assertEqual(code, 0)
        self.assertAlmostEqual(measure(out / "quiet.mp4").integrated, -14.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
