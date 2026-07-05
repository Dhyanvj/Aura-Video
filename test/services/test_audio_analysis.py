import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services import audio_analysis


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@unittest.skipUnless(_ffmpeg_available(), "ffmpeg/ffprobe not available on PATH")
class TestAudioAnalysis(unittest.TestCase):
    """
    Root-cause regression coverage: a TTS provider can report successful
    word/sentence-boundary metadata without the underlying audio payload
    actually being present or audible. These checks validate the real file
    via ffprobe/ffmpeg rather than trusting provider-supplied metadata.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="audio_analysis_test_")

        cls.audible_file = os.path.join(cls._tmp_dir, "audible.mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=5", cls.audible_file],
            capture_output=True,
            timeout=30,
        )

        cls.silent_file = os.path.join(cls._tmp_dir, "silent.mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "5", cls.silent_file],
            capture_output=True,
            timeout=30,
        )

        cls.too_short_file = os.path.join(cls._tmp_dir, "too_short.mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3", cls.too_short_file],
            capture_output=True,
            timeout=30,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_probe_audio_duration_missing_file_returns_zero(self):
        self.assertEqual(audio_analysis.probe_audio_duration("/tmp/definitely-does-not-exist.mp3"), 0.0)

    def test_probe_audio_duration_measures_real_file(self):
        self.assertAlmostEqual(audio_analysis.probe_audio_duration(self.audible_file), 5.0, delta=0.5)

    def test_measure_mean_volume_detects_audible_signal(self):
        mean_db = audio_analysis.measure_mean_volume_db(self.audible_file)
        self.assertIsNotNone(mean_db)
        self.assertGreater(mean_db, audio_analysis.SILENCE_THRESHOLD_DB)

    def test_measure_mean_volume_detects_silence(self):
        mean_db = audio_analysis.measure_mean_volume_db(self.silent_file)
        self.assertIsNotNone(mean_db)
        self.assertLess(mean_db, audio_analysis.SILENCE_THRESHOLD_DB)

    def test_check_audible_passes_for_real_audio(self):
        ok, reason = audio_analysis.check_audible(self.audible_file)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "")

    def test_check_audible_fails_for_silent_audio(self):
        ok, reason = audio_analysis.check_audible(self.silent_file)
        self.assertFalse(ok)
        self.assertIn("silent", reason)

    def test_check_audible_fails_for_too_short_audio(self):
        ok, reason = audio_analysis.check_audible(self.too_short_file)
        self.assertFalse(ok)
        self.assertIn("duration", reason)

    def test_check_audible_fails_for_missing_file(self):
        ok, reason = audio_analysis.check_audible("/tmp/definitely-does-not-exist.mp3")
        self.assertFalse(ok)
        self.assertIn("duration", reason)


if __name__ == "__main__":
    unittest.main()
