import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services import ai_images


class TestGenerateAiImageClip(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_zero_or_negative_duration_returns_none_without_any_network_call(self):
        with patch("app.services.ai_images.requests.get") as mock_get:
            self.assertIsNone(ai_images.generate_ai_image_clip("a prompt", 0, self._tmpdir))
            self.assertIsNone(ai_images.generate_ai_image_clip("a prompt", -1, self._tmpdir))
        mock_get.assert_not_called()

    def test_image_fetch_failure_returns_none(self):
        with patch("app.services.ai_images.requests.get", side_effect=ConnectionError("boom")):
            result = ai_images.generate_ai_image_clip("a prompt", 5, self._tmpdir)
        self.assertIsNone(result)

    def test_empty_image_response_returns_none(self):
        mock_response = MagicMock()
        mock_response.content = b""
        mock_response.raise_for_status = lambda: None
        with patch("app.services.ai_images.requests.get", return_value=mock_response):
            result = ai_images.generate_ai_image_clip("a prompt", 5, self._tmpdir)
        self.assertIsNone(result)

    def test_ffmpeg_failure_returns_none_and_cleans_up_the_image(self):
        mock_response = MagicMock()
        mock_response.content = b"fake-jpeg-bytes"
        mock_response.raise_for_status = lambda: None
        with patch("app.services.ai_images.requests.get", return_value=mock_response), patch(
            "app.services.ai_images.subprocess.run", side_effect=RuntimeError("ffmpeg boom")
        ):
            result = ai_images.generate_ai_image_clip("a prompt", 5, self._tmpdir)
        self.assertIsNone(result)
        # The downloaded image must not linger as orphaned disk clutter.
        self.assertEqual(os.listdir(self._tmpdir), [])

    def test_successful_generation_returns_the_clip_path(self):
        mock_response = MagicMock()
        mock_response.content = b"fake-jpeg-bytes"
        mock_response.raise_for_status = lambda: None

        def fake_run(cmd, **kwargs):
            # Simulate ffmpeg producing the output file (last positional arg).
            output_path = cmd[-1]
            with open(output_path, "wb") as f:
                f.write(b"fake-mp4-bytes")
            return MagicMock(returncode=0)

        with patch("app.services.ai_images.requests.get", return_value=mock_response), patch(
            "app.services.ai_images.subprocess.run", side_effect=fake_run
        ):
            result = ai_images.generate_ai_image_clip("a mantis shrimp", 5, self._tmpdir)

        self.assertIsNotNone(result)
        self.assertTrue(os.path.isfile(result))
        self.assertTrue(result.endswith(".mp4"))

    def test_prompt_is_slugified_for_the_filename(self):
        mock_response = MagicMock()
        mock_response.content = b"fake-jpeg-bytes"
        mock_response.raise_for_status = lambda: None

        def fake_run(cmd, **kwargs):
            with open(cmd[-1], "wb") as f:
                f.write(b"fake-mp4-bytes")
            return MagicMock(returncode=0)

        with patch("app.services.ai_images.requests.get", return_value=mock_response), patch(
            "app.services.ai_images.subprocess.run", side_effect=fake_run
        ):
            result = ai_images.generate_ai_image_clip("A Wild! Prompt/With Punctuation", 5, self._tmpdir)

        self.assertNotIn("!", result)
        self.assertNotIn("/", os.path.basename(result))


if __name__ == "__main__":
    unittest.main()
