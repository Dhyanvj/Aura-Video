import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services import qa


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@unittest.skipUnless(_ffmpeg_available(), "ffmpeg/ffprobe not available on PATH")
class TestQATechnicalChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="qa_test_")

        # A valid 20s, 1080x1920, audio-bearing sample - should pass every check.
        cls.good_video = os.path.join(cls._tmp_dir, "good.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=24:duration=20",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", cls.good_video,
            ],
            capture_output=True,
            timeout=60,
        )

        # Wrong resolution, no audio, and too short - should fail multiple checks.
        cls.bad_video = os.path.join(cls._tmp_dir, "bad.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=640x480:rate=24:duration=3",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", cls.bad_video,
            ],
            capture_output=True,
            timeout=60,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_missing_file_reports_a_single_failing_check(self):
        checks, duration = qa.run_technical_checks("/tmp/definitely-does-not-exist.mp4")
        self.assertEqual(duration, 0.0)
        self.assertEqual(len(checks), 1)
        self.assertFalse(checks[0].passed)
        self.assertEqual(checks[0].name, "file_exists")

    def test_valid_video_passes_all_technical_checks(self):
        checks, duration = qa.run_technical_checks(self.good_video)
        self.assertAlmostEqual(duration, 20.0, delta=0.5)
        by_name = {c.name: c for c in checks}
        self.assertTrue(by_name["file_size"].passed)
        self.assertTrue(by_name["duration_15_to_60s"].passed)
        self.assertTrue(by_name["resolution_1080x1920"].passed)
        self.assertTrue(by_name["audio_present"].passed)

    def test_invalid_video_fails_resolution_duration_and_audio_checks(self):
        checks, duration = qa.run_technical_checks(self.bad_video)
        self.assertAlmostEqual(duration, 3.0, delta=0.5)
        by_name = {c.name: c for c in checks}
        self.assertFalse(by_name["duration_15_to_60s"].passed)
        self.assertFalse(by_name["resolution_1080x1920"].passed)
        self.assertFalse(by_name["audio_present"].passed)

    def test_audio_duration_check_skipped_without_expected_duration(self):
        checks, _ = qa.run_technical_checks(self.good_video)
        self.assertNotIn("audio_duration_matches_voiceover", {c.name for c in checks})

    def test_audio_duration_matches_voiceover_passes_within_tolerance(self):
        # good_video is a real 20s render; well within 2% of 20.0s.
        checks, _ = qa.run_technical_checks(self.good_video, expected_audio_duration=20.0)
        by_name = {c.name: c for c in checks}
        self.assertTrue(by_name["audio_duration_matches_voiceover"].passed)

    def test_audio_duration_matches_voiceover_fails_when_final_mux_dropped_audio_time(self):
        # Root-cause regression: a truncated/dropped audio track during the
        # final render would still pass "audio_present" (stream exists)
        # undetected - this check catches the duration mismatch instead.
        checks, _ = qa.run_technical_checks(self.good_video, expected_audio_duration=10.0)
        by_name = {c.name: c for c in checks}
        self.assertFalse(by_name["audio_duration_matches_voiceover"].passed)

    def test_extract_frames_returns_evenly_spaced_real_jpegs(self):
        frames = qa.extract_frames(self.good_video, video_duration=20.0, count=6)
        try:
            self.assertEqual(len(frames), 6)
            for frame_path in frames:
                self.assertTrue(os.path.isfile(frame_path))
                self.assertGreater(os.path.getsize(frame_path), 0)
        finally:
            if frames:
                shutil.rmtree(os.path.dirname(frames[0]), ignore_errors=True)

    def test_extract_frames_returns_empty_for_zero_duration(self):
        self.assertEqual(qa.extract_frames(self.good_video, video_duration=0.0), [])


if __name__ == "__main__":
    unittest.main()
