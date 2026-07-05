import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.agents.quality_reviewer import QualityReviewer
from app.agents.schemas import FrameFinding, QuoteOrLesson, VisionReview
from app.services.qa import TechnicalCheckResult


class TestQualityReviewerRevisionRouting(unittest.TestCase):
    """
    Root-cause regression coverage: duration overruns were being routed to
    revision_target="producer", which never touches the script - the one
    thing that can actually fix spoken duration - so videos kept cycling
    through revisions and hitting the max_revisions cap without the duration
    problem ever getting fixed. Only a script-level change (fewer words)
    fixes duration_15_to_60s; Producer re-rendering different footage does
    nothing about it.
    """

    def _review_with(self, technical_checks, vision_overall, vision_revision_target):
        reviewer = QualityReviewer(project_id=None)
        with patch(
            "app.agents.quality_reviewer.qa_service.run_technical_checks",
            return_value=(technical_checks, 70.0),
        ), patch(
            "app.agents.quality_reviewer.qa_service.extract_frames", return_value=["/tmp/frame1.jpg"]
        ), patch.object(QualityReviewer, "_encode_image", return_value="ZmFrZQ=="), patch.object(
            reviewer,
            "call_json_with_content",
            return_value=VisionReview(
                overall=vision_overall,
                frame_findings=[FrameFinding(frame_index=0, matches_script=True, notes="ok")],
                revision_target=vision_revision_target,
                revision_notes="vision notes",
            ),
        ):
            return reviewer.review(video_path="/tmp/whatever.mp4", script="script")

    def test_duration_failure_routes_to_creative_director_even_if_vision_says_pass(self):
        report = self._review_with(
            technical_checks=[TechnicalCheckResult("duration_15_to_60s", False, "70.0s")],
            vision_overall="pass",
            vision_revision_target=None,
        )
        self.assertEqual(report.overall, "revise")
        self.assertEqual(report.revision_target, "creative_director")

    def test_duration_failure_overrides_visions_own_producer_guess(self):
        # Vision itself may already say "revise"/"producer" (it can see the
        # checklist too and sometimes mislabels this) - the deterministic
        # check result must still win.
        report = self._review_with(
            technical_checks=[TechnicalCheckResult("duration_15_to_60s", False, "70.0s")],
            vision_overall="revise",
            vision_revision_target="producer",
        )
        self.assertEqual(report.revision_target, "creative_director")

    def test_non_duration_technical_failure_still_routes_to_producer(self):
        report = self._review_with(
            technical_checks=[TechnicalCheckResult("resolution_1080x1920", False, "640x480")],
            vision_overall="pass",
            vision_revision_target=None,
        )
        self.assertEqual(report.overall, "revise")
        self.assertEqual(report.revision_target, "producer")

    def test_vision_content_mismatch_with_passing_technical_checks_keeps_producer_target(self):
        report = self._review_with(
            technical_checks=[TechnicalCheckResult("duration_15_to_60s", True, "45.0s")],
            vision_overall="revise",
            vision_revision_target="producer",
        )
        self.assertEqual(report.revision_target, "producer")

    def test_all_checks_pass_and_vision_passes_is_a_clean_pass(self):
        report = self._review_with(
            technical_checks=[TechnicalCheckResult("duration_15_to_60s", True, "45.0s")],
            vision_overall="pass",
            vision_revision_target=None,
        )
        self.assertEqual(report.overall, "pass")
        self.assertIsNone(report.revision_target)

    def test_review_forwards_expected_audio_duration_to_technical_checks(self):
        reviewer = QualityReviewer(project_id=None)
        with patch(
            "app.agents.quality_reviewer.qa_service.run_technical_checks",
            return_value=([TechnicalCheckResult("duration_15_to_60s", True, "45.0s")], 45.0),
        ) as mock_checks, patch(
            "app.agents.quality_reviewer.qa_service.extract_frames", return_value=["/tmp/frame1.jpg"]
        ), patch.object(QualityReviewer, "_encode_image", return_value="ZmFrZQ=="), patch.object(
            reviewer,
            "call_json_with_content",
            return_value=VisionReview(
                overall="pass", frame_findings=[FrameFinding(frame_index=0, matches_script=True, notes="ok")]
            ),
        ):
            reviewer.review(
                video_path="/tmp/whatever.mp4",
                script="script",
                subtitle_path="/tmp/sub.srt",
                expected_audio_duration=45.2,
            )

        mock_checks.assert_called_once_with("/tmp/whatever.mp4", "/tmp/sub.srt", 45.2)


class TestQualityReviewerAttributionCheck(unittest.TestCase):
    """
    Part 2 (Motivational Quotes & Life Lessons): "misattributed quotes are a
    QA fail" - there's no independent source lookup yet (that's the
    Researcher agent), so this checks the vision model's own knowledge-based
    assessment and treats anything short of "correct" as unsafe to publish.
    """

    def _review_with_quote(self, quote_attribution_check, is_quote=True):
        reviewer = QualityReviewer(project_id=None)
        quote = QuoteOrLesson(is_quote=is_quote, text="The obstacle is the way.", attribution="Ryan Holiday")
        with patch(
            "app.agents.quality_reviewer.qa_service.run_technical_checks",
            return_value=([TechnicalCheckResult("duration_15_to_60s", True, "45.0s")], 45.0),
        ), patch(
            "app.agents.quality_reviewer.qa_service.extract_frames", return_value=["/tmp/frame1.jpg"]
        ), patch.object(QualityReviewer, "_encode_image", return_value="ZmFrZQ=="), patch.object(
            reviewer,
            "call_json_with_content",
            return_value=VisionReview(
                overall="pass",
                frame_findings=[FrameFinding(frame_index=0, matches_script=True, notes="ok")],
                quote_attribution_check=quote_attribution_check,
            ),
        ):
            return reviewer.review(video_path="/tmp/whatever.mp4", script="script", quote_or_lesson=quote)

    def test_incorrect_attribution_is_a_hard_fail(self):
        report = self._review_with_quote("incorrect")
        self.assertEqual(report.overall, "fail")
        self.assertEqual(report.revision_target, "creative_director")
        self.assertIn("could not be confirmed", report.revision_notes)

    def test_uncertain_attribution_is_a_revision_not_an_outright_fail(self):
        report = self._review_with_quote("uncertain")
        self.assertEqual(report.overall, "revise")
        self.assertEqual(report.revision_target, "creative_director")

    def test_correct_attribution_does_not_affect_a_clean_pass(self):
        report = self._review_with_quote("correct")
        self.assertEqual(report.overall, "pass")

    def test_attribution_check_ignored_for_a_life_lesson(self):
        # is_quote=False means there's no attribution to verify at all.
        report = self._review_with_quote("incorrect", is_quote=False)
        self.assertEqual(report.overall, "pass")

    def test_no_quote_supplied_is_unaffected(self):
        reviewer = QualityReviewer(project_id=None)
        with patch(
            "app.agents.quality_reviewer.qa_service.run_technical_checks",
            return_value=([TechnicalCheckResult("duration_15_to_60s", True, "45.0s")], 45.0),
        ), patch(
            "app.agents.quality_reviewer.qa_service.extract_frames", return_value=["/tmp/frame1.jpg"]
        ), patch.object(QualityReviewer, "_encode_image", return_value="ZmFrZQ=="), patch.object(
            reviewer,
            "call_json_with_content",
            return_value=VisionReview(
                overall="pass", frame_findings=[FrameFinding(frame_index=0, matches_script=True, notes="ok")]
            ),
        ):
            report = reviewer.review(video_path="/tmp/whatever.mp4", script="script", quote_or_lesson=None)
        self.assertEqual(report.overall, "pass")


if __name__ == "__main__":
    unittest.main()
