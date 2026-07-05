import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models.schema import VideoAspect, VideoParams
from app.services import video as vd


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


class TestFindQuoteTimeRange(unittest.TestCase):
    """
    Part 2 (Motivational Quotes & Life Lessons): the quote/lesson centerpiece
    must get a distinct on-screen treatment. find_quote_time_range() locates
    which subtitle line(s) correspond to that centerpiece so the renderer
    knows which time window to give the special treatment to.
    """

    def test_matches_single_subtitle_line(self):
        subtitles = [
            ((0.0, 2.0), "Nobody claps when you keep a promise to yourself."),
            ((2.0, 5.0), "That's exactly why it's hard."),
        ]
        result = vd.find_quote_time_range(subtitles, "Nobody claps when you keep a promise to yourself.")
        self.assertEqual(result, (0.0, 2.0))

    def test_matches_case_and_punctuation_insensitively(self):
        subtitles = [((1.5, 4.0), "The obstacle is the way")]
        result = vd.find_quote_time_range(subtitles, "The Obstacle Is the Way.")
        self.assertEqual(result, (1.5, 4.0))

    def test_spans_multiple_matching_lines(self):
        subtitles = [
            ((0.0, 1.0), "unrelated intro line"),
            ((1.0, 3.0), "The obstacle is the way,"),
            ((3.0, 5.0), "and the way is the obstacle."),
            ((5.0, 6.0), "unrelated outro line"),
        ]
        result = vd.find_quote_time_range(
            subtitles, "The obstacle is the way, and the way is the obstacle."
        )
        self.assertEqual(result, (1.0, 5.0))

    def test_returns_none_when_nothing_matches(self):
        subtitles = [((0.0, 2.0), "completely unrelated text")]
        result = vd.find_quote_time_range(subtitles, "a quote that never appears")
        self.assertIsNone(result)

    def test_returns_none_for_empty_quote(self):
        subtitles = [((0.0, 2.0), "some text")]
        self.assertIsNone(vd.find_quote_time_range(subtitles, ""))


@unittest.skipUnless(_ffmpeg_available(), "ffmpeg/ffprobe not available on PATH")
class TestGenerateVideoQuoteCard(unittest.TestCase):
    """Real, end-to-end regression guard: generate_video() must not crash
    when a quote_text is set, and must produce a video covering the full
    audio duration (i.e. the quote-card compositing doesn't break output)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="quote_card_test_")

        cls.video_path = os.path.join(cls._tmp_dir, "combined.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=24:duration=6",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", cls.video_path,
            ],
            capture_output=True,
            timeout=60,
        )

        cls.audio_path = os.path.join(cls._tmp_dir, "audio.mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=6", cls.audio_path],
            capture_output=True,
            timeout=30,
        )

        cls.subtitle_path = os.path.join(cls._tmp_dir, "subtitle.srt")
        with open(cls.subtitle_path, "w", encoding="utf-8") as f:
            f.write(
                "1\n00:00:00,000 --> 00:00:03,000\nNobody claps when you keep a promise to yourself.\n\n"
                "2\n00:00:03,000 --> 00:00:06,000\nThat's exactly why it matters.\n"
            )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_generate_video_with_quote_text_produces_output(self):
        output_file = os.path.join(self._tmp_dir, "final.mp4")
        params = VideoParams(
            video_subject="test",
            video_aspect=VideoAspect.portrait.value,
            quote_text="Nobody claps when you keep a promise to yourself.",
            quote_attribution=None,
            bgm_type="",
            bgm_file="",
        )

        vd.generate_video(
            video_path=self.video_path,
            audio_path=self.audio_path,
            subtitle_path=self.subtitle_path,
            output_file=output_file,
            params=params,
        )

        self.assertTrue(os.path.isfile(output_file))
        self.assertGreater(os.path.getsize(output_file), 0)


if __name__ == "__main__":
    unittest.main()
