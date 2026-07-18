import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.agents.retrospective import Retrospective
from app.agents.schemas import RetrospectiveLesson, RetrospectiveResult


class TestRetrospective(unittest.TestCase):
    def test_always_uses_haiku_regardless_of_configured_model(self):
        retro = Retrospective(project_id=None)
        self.assertEqual(retro.model, "claude-haiku-4-5")

    def test_run_returns_lessons_from_the_llm_call(self):
        retro = Retrospective(project_id=None)
        fake_result = RetrospectiveResult(
            lessons=[RetrospectiveLesson(agent="creative_director", actionable_rule="be more concrete")]
        )
        with patch.object(retro, "call_json", return_value=fake_result) as mock_call:
            lessons = retro.run(
                qa_reports=[{"overall": "pass"}],
                human_edits=[{"field": "title", "before": "A", "after": "B"}],
                revision_notes_history=["tighten the hook"],
                script="a script",
                content_type_id="fun_facts",
            )

        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0].actionable_rule, "be more concrete")
        _, kwargs = mock_call.call_args
        self.assertIn("tighten the hook", kwargs["user"])
        self.assertIn("fun_facts", kwargs["user"])

    def test_run_returns_empty_list_when_no_lessons_found(self):
        retro = Retrospective(project_id=None)
        with patch.object(retro, "call_json", return_value=RetrospectiveResult(lessons=[])):
            lessons = retro.run(
                qa_reports=[], human_edits=[], revision_notes_history=[], script="s", content_type_id=None
            )
        self.assertEqual(lessons, [])

    def test_overridden_findings_are_forwarded_to_the_llm_call(self):
        # Incident fix §2: a repeatedly-overridden finding type is exactly
        # the signal this retrospective should be able to notice and
        # propose recalibrating.
        retro = Retrospective(project_id=None)
        fake_result = RetrospectiveResult(
            lessons=[
                RetrospectiveLesson(
                    agent="quality_reviewer",
                    actionable_rule="humans keep overriding the dark-frame finding; consider downgrading it",
                )
            ]
        )
        overridden = [{"at": "2026-01-01T00:00:00", "fingerprints": ["visual:frame7"], "findings": []}]
        with patch.object(retro, "call_json", return_value=fake_result) as mock_call:
            lessons = retro.run(
                qa_reports=[],
                human_edits=[],
                revision_notes_history=[],
                script="s",
                content_type_id="motivational",
                overridden_findings=overridden,
            )

        self.assertEqual(len(lessons), 1)
        _, kwargs = mock_call.call_args
        self.assertIn("visual:frame7", kwargs["user"])

    def test_overridden_findings_defaults_to_empty_when_omitted(self):
        retro = Retrospective(project_id=None)
        with patch.object(retro, "call_json", return_value=RetrospectiveResult(lessons=[])) as mock_call:
            retro.run(qa_reports=[], human_edits=[], revision_notes_history=[], script="s", content_type_id=None)
        _, kwargs = mock_call.call_args
        self.assertIn('"qa_findings_overridden_by_human": []', kwargs["user"])


if __name__ == "__main__":
    unittest.main()
