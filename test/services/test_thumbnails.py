import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PIL import Image

from app.services import thumbnails


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@unittest.skipUnless(_ffmpeg_available(), "ffmpeg/ffprobe not available on PATH")
class TestGenerateThumbnailCandidates(unittest.TestCase):
    """
    Thumbnails must be JPEG, not PNG - directly downloadable/usable as a
    platform-ready thumbnail without a separate export step.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="thumbnails_test_")
        cls.video_path = os.path.join(cls._tmp_dir, "video.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=24:duration=10",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", cls.video_path,
            ],
            capture_output=True,
            timeout=60,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_candidates_are_jpg_files(self):
        out_dir = tempfile.mkdtemp(prefix="thumbnails_out_")
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)

        candidates = thumbnails.generate_thumbnail_candidates(
            video_path=self.video_path, video_duration=10.0, hook_text="A short hook", out_dir=out_dir
        )

        self.assertEqual(len(candidates), 3)
        for path in candidates:
            self.assertTrue(path.endswith(".jpg"), path)
            self.assertTrue(os.path.isfile(path))
            with Image.open(path) as img:
                self.assertEqual(img.format, "JPEG")

    def test_no_raw_jpg_leftovers_in_output_dir(self):
        out_dir = tempfile.mkdtemp(prefix="thumbnails_out_")
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)

        thumbnails.generate_thumbnail_candidates(
            video_path=self.video_path, video_duration=10.0, hook_text="hook", out_dir=out_dir
        )
        leftovers = [f for f in os.listdir(out_dir) if f.startswith("thumb-raw-")]
        self.assertEqual(leftovers, [])

    def test_zero_duration_returns_no_candidates(self):
        self.assertEqual(
            thumbnails.generate_thumbnail_candidates(
                video_path=self.video_path, video_duration=0.0, hook_text="hook", out_dir=tempfile.mkdtemp()
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
