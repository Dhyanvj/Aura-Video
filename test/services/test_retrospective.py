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


if __name__ == "__main__":
    unittest.main()
