import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.agents.quality_reviewer import QualityReviewer
from app.agents.schemas import FrameFinding, VisionReview
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


if __name__ == "__main__":
    unittest.main()
