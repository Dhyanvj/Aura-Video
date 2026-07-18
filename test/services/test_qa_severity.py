import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.agents.schemas import Finding, QuoteOrLesson, ResearchDossier, VerifiedQuote
from app.services import qa


class TestCheckSeverityMapping(unittest.TestCase):
    """
    Incident fix §1: every deterministic check must have a severity mapped
    in code, never inferred - and duration stays "major" (a revision can
    plausibly fix it) while file/audio/resolution problems that make the
    video unusable are "critical".
    """

    def test_hard_technical_criticals(self):
        for name in ("file_exists", "file_size", "ffprobe", "audio_present", "audio_duration_matches_voiceover"):
            self.assertEqual(qa.severity_for_check(name), "critical", name)
            self.assertFalse(qa.is_overridable_check(name), name)

    def test_resolution_is_critical_but_overridable(self):
        # Wrong resolution is critical (unusable/dangerous to publish per the
        # spec) but is NOT a hard technical critical - a human can still
        # decide to override it, unlike missing/corrupt audio.
        self.assertEqual(qa.severity_for_check("resolution_1080x1920"), "critical")
        self.assertTrue(qa.is_overridable_check("resolution_1080x1920"))

    def test_duration_is_major(self):
        self.assertEqual(qa.severity_for_check("duration_15_to_60s"), "major")
        self.assertTrue(qa.is_overridable_check("duration_15_to_60s"))

    def test_subtitle_alignment_is_major(self):
        self.assertEqual(qa.severity_for_check("subtitle_alignment"), "major")

    def test_unknown_check_defaults_to_major(self):
        self.assertEqual(qa.severity_for_check("some_future_check"), "major")


def _finding(severity: str, category: str = "technical", target=None) -> Finding:
    return Finding(category=category, fingerprint=f"{category}:{severity}", severity=severity, message="m", revision_target=target)


class TestAggregateVerdict(unittest.TestCase):
    def test_no_findings_is_a_clean_pass(self):
        self.assertEqual(qa.aggregate_verdict([]), "pass")

    def test_only_minor_findings_is_pass_with_warnings(self):
        findings = [_finding("minor"), _finding("minor")]
        self.assertEqual(qa.aggregate_verdict(findings), "pass_with_warnings")

    def test_any_major_finding_is_revise(self):
        findings = [_finding("minor"), _finding("major")]
        self.assertEqual(qa.aggregate_verdict(findings), "revise")

    def test_any_critical_finding_is_fail(self):
        findings = [_finding("minor"), _finding("major"), _finding("critical")]
        self.assertEqual(qa.aggregate_verdict(findings), "fail")


class TestPickRevisionTarget(unittest.TestCase):
    def test_no_actionable_findings_returns_none(self):
        self.assertIsNone(qa.pick_revision_target([_finding("minor", target="producer")]))

    def test_script_only_technical_beats_visual_producer_guess(self):
        findings = [
            _finding("major", category="technical", target="creative_director"),
            _finding("major", category="visual", target="producer"),
        ]
        self.assertEqual(qa.pick_revision_target(findings), "creative_director")

    def test_quote_attribution_beats_everything(self):
        findings = [
            _finding("major", category="technical", target="producer"),
            _finding("major", category="quote_attribution", target="researcher"),
        ]
        self.assertEqual(qa.pick_revision_target(findings), "researcher")

    def test_technical_producer_bucket_used_when_nothing_else_present(self):
        findings = [_finding("critical", category="technical", target="producer")]
        self.assertEqual(qa.pick_revision_target(findings), "producer")


class TestVerifyQuoteAgainstDossier(unittest.TestCase):
    def test_no_verified_quote_is_major(self):
        quote = QuoteOrLesson(is_quote=True, text="Some quote.", attribution="Someone")
        issue = qa.verify_quote_against_dossier(quote, ResearchDossier(topic="t"))
        self.assertIsNotNone(issue)
        self.assertEqual(issue[1], "major")

    def test_verified_matching_quote_passes(self):
        quote = QuoteOrLesson(is_quote=True, text="Be the change you wish to see.", attribution="Gandhi")
        dossier = ResearchDossier(
            topic="t",
            verified_quote=VerifiedQuote(
                text="Be the change you wish to see.", attribution="Gandhi", verification_status="verified"
            ),
        )
        self.assertIsNone(qa.verify_quote_against_dossier(quote, dossier))

    def test_verified_quote_tolerates_punctuation_and_case_differences(self):
        quote = QuoteOrLesson(is_quote=True, text="be the change you wish to see", attribution="gandhi")
        dossier = ResearchDossier(
            topic="t",
            verified_quote=VerifiedQuote(
                text="Be the change you wish to see!", attribution="Gandhi", verification_status="verified"
            ),
        )
        self.assertIsNone(qa.verify_quote_against_dossier(quote, dossier))

    def test_wording_mismatch_against_verified_quote_is_major(self):
        quote = QuoteOrLesson(is_quote=True, text="Totally different wording entirely here.", attribution="Gandhi")
        dossier = ResearchDossier(
            topic="t",
            verified_quote=VerifiedQuote(
                text="Be the change you wish to see.", attribution="Gandhi", verification_status="verified"
            ),
        )
        issue = qa.verify_quote_against_dossier(quote, dossier)
        self.assertEqual(issue[1], "major")

    def test_disputed_matching_quote_is_critical(self):
        quote = QuoteOrLesson(is_quote=True, text="Be the change you wish to see.", attribution="Gandhi")
        dossier = ResearchDossier(
            topic="t",
            verified_quote=VerifiedQuote(
                text="Be the change you wish to see.", attribution="Gandhi", verification_status="disputed"
            ),
        )
        issue = qa.verify_quote_against_dossier(quote, dossier)
        self.assertEqual(issue[1], "critical")


if __name__ == "__main__":
    unittest.main()
