import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.agents.quality_reviewer import QualityReviewer
from app.agents.schemas import (
    FactCheckFlag,
    FactCheckResult,
    FrameFinding,
    QuoteOrLesson,
    ResearchDossier,
    VerifiedQuote,
    VisionReview,
)
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
        # Wrong resolution is severity="critical" (qa.CHECK_SEVERITY) per the
        # incident fix's severity tiers, so the verdict is "fail" now, not
        # "revise" - but it's still overridable (not a hard technical
        # critical) and still routes to producer, same as before.
        report = self._review_with(
            technical_checks=[TechnicalCheckResult("resolution_1080x1920", False, "640x480")],
            vision_overall="pass",
            vision_revision_target=None,
        )
        self.assertEqual(report.overall, "fail")
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


class TestQualityReviewerQuoteVerification(unittest.TestCase):
    """
    Incident fix §3: attribution verification happens pre-production (the
    Researcher verifies a quote from >=2 sources before the script gate) and
    QA validates the script against that dossier rather than re-deriving its
    own opinion from an LLM's training knowledge - that re-litigation is
    exactly what let a correctly-attributed, already-verified quote (the
    "Earth is the cradle of humanity" / Tsiolkovsky incident) get flagged
    "uncertain" post-render.
    """

    def _review_with_quote(self, quote_text, attribution, dossier, is_quote=True):
        reviewer = QualityReviewer(project_id=None)
        quote = QuoteOrLesson(is_quote=is_quote, text=quote_text, attribution=attribution)
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
        ), patch.object(reviewer, "call_json", return_value=FactCheckResult(flags=[])):
            return reviewer.review(
                video_path="/tmp/whatever.mp4", script="script", quote_or_lesson=quote, research_dossier=dossier
            )

    def test_dossier_verified_matching_quote_is_a_clean_pass(self):
        # QA must accept a dossier-verified quote outright, not re-derive its
        # own opinion - this is the exact incident regression.
        dossier = ResearchDossier(
            topic="t",
            verified_quote=VerifiedQuote(
                text="The obstacle is the way.", attribution="Ryan Holiday", verification_status="verified"
            ),
        )
        report = self._review_with_quote("The obstacle is the way.", "Ryan Holiday", dossier)
        self.assertEqual(report.overall, "pass")
        self.assertEqual(report.findings, [])

    def test_dossier_verified_quote_tolerates_punctuation_differences(self):
        dossier = ResearchDossier(
            topic="t",
            verified_quote=VerifiedQuote(
                text="The obstacle is the way!", attribution="Ryan Holiday", verification_status="verified"
            ),
        )
        report = self._review_with_quote("The obstacle is the way.", "Ryan Holiday", dossier)
        self.assertEqual(report.overall, "pass")

    def test_no_verified_quote_in_dossier_is_major_not_critical(self):
        dossier = ResearchDossier(topic="t")  # verified_quote left unset
        report = self._review_with_quote("The obstacle is the way.", "Ryan Holiday", dossier)
        self.assertEqual(report.overall, "revise")
        self.assertEqual(report.revision_target, "researcher")
        finding = next(f for f in report.findings if f.category == "quote_attribution")
        self.assertEqual(finding.severity, "major")

    def test_disputed_verified_quote_is_critical(self):
        dossier = ResearchDossier(
            topic="t",
            verified_quote=VerifiedQuote(
                text="The obstacle is the way.", attribution="Ryan Holiday", verification_status="disputed"
            ),
        )
        report = self._review_with_quote("The obstacle is the way.", "Ryan Holiday", dossier)
        self.assertEqual(report.overall, "fail")
        self.assertEqual(report.revision_target, "researcher")
        finding = next(f for f in report.findings if f.category == "quote_attribution")
        self.assertEqual(finding.severity, "critical")

    def test_mismatched_attribution_against_verified_quote_is_major(self):
        dossier = ResearchDossier(
            topic="t",
            verified_quote=VerifiedQuote(
                text="The obstacle is the way.", attribution="Ryan Holiday", verification_status="verified"
            ),
        )
        report = self._review_with_quote("The obstacle is the way.", "Marcus Aurelius", dossier)
        self.assertEqual(report.overall, "revise")
        self.assertEqual(report.revision_target, "researcher")

    def test_attribution_check_ignored_for_a_life_lesson(self):
        # is_quote=False means there's no attribution to verify at all.
        report = self._review_with_quote("Some lesson.", None, ResearchDossier(topic="t"), is_quote=False)
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


class TestQualityReviewerFactCheck(unittest.TestCase):
    """
    Part 3: a Researcher dossier is now available for fact-checking the
    script against - a hallucinated claim that drifts from what was actually
    verified must send the video back to the Creative Director, not just be
    silently published because the vision review looked fine.
    """

    def _review_with_fact_check(self, flags, vision_overall="pass"):
        reviewer = QualityReviewer(project_id=None)
        dossier = ResearchDossier(topic="an octopus fact", key_facts=[])
        with patch(
            "app.agents.quality_reviewer.qa_service.run_technical_checks",
            return_value=([TechnicalCheckResult("duration_15_to_60s", True, "45.0s")], 45.0),
        ), patch(
            "app.agents.quality_reviewer.qa_service.extract_frames", return_value=["/tmp/frame1.jpg"]
        ), patch.object(QualityReviewer, "_encode_image", return_value="ZmFrZQ=="), patch.object(
            reviewer,
            "call_json_with_content",
            return_value=VisionReview(
                overall=vision_overall, frame_findings=[FrameFinding(frame_index=0, matches_script=True, notes="ok")]
            ),
        ), patch.object(reviewer, "call_json", return_value=FactCheckResult(flags=flags)):
            return reviewer.review(video_path="/tmp/whatever.mp4", script="script", research_dossier=dossier)

    def test_unsupported_claim_routes_to_researcher(self):
        # Incident fix §4: an unsupported factual claim needs evidence, not a
        # rewrite - it must route to the Researcher for supplementary
        # verification, never straight to the Creative Director (who can
        # only reword, not confirm a fact).
        report = self._review_with_fact_check(
            [FactCheckFlag(sentence="Octopuses have nine hearts.", supported=False, note="dossier says three")]
        )
        self.assertEqual(report.overall, "revise")
        self.assertEqual(report.revision_target, "researcher")
        self.assertIn("Octopuses have nine hearts", report.revision_notes)
        self.assertEqual(len(report.fact_check_flags), 1)

    def test_all_supported_claims_do_not_affect_a_clean_pass(self):
        report = self._review_with_fact_check(
            [FactCheckFlag(sentence="Octopuses have three hearts.", supported=True)]
        )
        self.assertEqual(report.overall, "pass")
        self.assertEqual(len(report.fact_check_flags), 1)

    def test_no_dossier_skips_fact_check_entirely(self):
        reviewer = QualityReviewer(project_id=None)
        with patch(
            "app.agents.quality_reviewer.qa_service.run_technical_checks",
            return_value=([TechnicalCheckResult("duration_15_to_60s", True, "45.0s")], 45.0),
        ), patch(
            "app.agents.quality_reviewer.qa_service.extract_frames", return_value=["/tmp/frame1.jpg"]
        ), patch.object(QualityReviewer, "_encode_image", return_value="ZmFrZQ=="), patch.object(
            reviewer,
            "call_json_with_content",
            return_value=VisionReview(overall="pass", frame_findings=[FrameFinding(frame_index=0, matches_script=True, notes="ok")]),
        ), patch.object(reviewer, "call_json") as mock_fact_check:
            report = reviewer.review(video_path="/tmp/whatever.mp4", script="script", research_dossier=None)
        mock_fact_check.assert_not_called()
        self.assertEqual(report.fact_check_flags, [])

    def test_unsupported_claim_note_appended_without_overriding_an_existing_critical(self):
        # A separate critical finding (a disputed/debunked quote) must still
        # win as the overall verdict - the fact-check note is additive
        # (still present in findings/revision_notes), not a demotion of the
        # critical severity down to major.
        reviewer = QualityReviewer(project_id=None)
        quote = QuoteOrLesson(is_quote=True, text="The obstacle is the way.", attribution="Ryan Holiday")
        dossier = ResearchDossier(
            topic="t",
            verified_quote=VerifiedQuote(
                text="The obstacle is the way.", attribution="Ryan Holiday", verification_status="disputed"
            ),
        )
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
            ),
        ), patch.object(
            reviewer,
            "call_json",
            return_value=FactCheckResult(flags=[FactCheckFlag(sentence="x", supported=False)]),
        ):
            report = reviewer.review(
                video_path="/tmp/whatever.mp4", script="script", quote_or_lesson=quote, research_dossier=dossier
            )
        self.assertEqual(report.overall, "fail")
        self.assertIn('"x"', report.revision_notes)
        categories = {f.category for f in report.findings}
        self.assertIn("quote_attribution", categories)
        self.assertIn("fact_check", categories)


class TestQualityReviewerScriptRepetition(unittest.TestCase):
    """
    docs/DECISIONS_V3.md §2: a deterministic n-gram overlap check against the
    last 5 scripts of this content type, independent of the LLM vision call -
    variety beyond just topic-level dedupe.
    """

    def _review(self, script, prior_scripts):
        reviewer = QualityReviewer(project_id=None)
        with patch(
            "app.agents.quality_reviewer.qa_service.run_technical_checks",
            return_value=([], 30.0),
        ), patch(
            "app.agents.quality_reviewer.qa_service.extract_frames", return_value=["/tmp/frame1.jpg"]
        ), patch.object(QualityReviewer, "_encode_image", return_value="ZmFrZQ=="), patch.object(
            reviewer,
            "call_json_with_content",
            return_value=VisionReview(
                overall="pass", frame_findings=[FrameFinding(frame_index=0, matches_script=True, notes="ok")]
            ),
        ):
            return reviewer.review(video_path="/tmp/whatever.mp4", script=script, prior_scripts=prior_scripts)

    def test_near_identical_script_flags_and_downgrades_a_pass_to_revise(self):
        script = "The mantis shrimp punches faster than a speeding bullet in the open ocean today."
        prior = ["The mantis shrimp punches faster than a speeding bullet in the open ocean every day."]
        report = self._review(script, prior)
        self.assertEqual(report.overall, "revise")
        self.assertEqual(report.revision_target, "creative_director")
        self.assertIsNotNone(report.script_repetition_flag)
        self.assertIn("overlap", report.script_repetition_flag)

    def test_dissimilar_scripts_are_unaffected(self):
        script = "The mantis shrimp punches faster than a speeding bullet in the open ocean today."
        prior = ["Discipline means keeping a promise to yourself when nobody else is watching you."]
        report = self._review(script, prior)
        self.assertEqual(report.overall, "pass")
        self.assertIsNone(report.script_repetition_flag)

    def test_no_prior_scripts_skips_the_check_entirely(self):
        script = "The mantis shrimp punches faster than a speeding bullet in the open ocean today."
        report = self._review(script, prior_scripts=None)
        self.assertEqual(report.overall, "pass")
        self.assertIsNone(report.script_repetition_flag)


if __name__ == "__main__":
    unittest.main()
