import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.agents import creative_director
from app.agents.creative_director import CreativeDirector
from app.agents.schemas import SearchTermsRevision


class TestCreativeDirectorTargetedRevision(unittest.TestCase):
    def test_revise_search_terms_freezes_script_and_returns_new_terms(self):
        director = CreativeDirector(project_id=None)
        with patch.object(
            director, "call_json", return_value=SearchTermsRevision(search_terms=["a", "b", "c"])
        ) as mock_call:
            terms = director.revise_search_terms(
                script="Some frozen script.", niche="ocean facts", revision_notes="wrong species shown"
            )

        self.assertEqual(terms, ["a", "b", "c"])
        _, kwargs = mock_call.call_args
        self.assertIn("wrong species shown", kwargs["user"])
        self.assertIn("Some frozen script.", kwargs["user"])
        # The script/voice-frozen framing must be explicit, not implied -
        # this is what stops the model from silently rewriting the script.
        normalized_system = " ".join(kwargs["system"].split())
        self.assertIn("must not change", normalized_system)

    def test_write_appends_revision_notes_to_system_prompt_and_payload(self):
        director = CreativeDirector(project_id=None)
        with patch.object(director, "call_json") as mock_call:
            director.write(topic="whales", niche="ocean", revision_notes="tighten the hook")

        _, kwargs = mock_call.call_args
        self.assertIn("tighten the hook", kwargs["system"])
        self.assertIn("tighten the hook", kwargs["user"])

    def test_word_count_target_leaves_real_margin_under_the_60s_cap(self):
        # Regression guard: the previous "140-160 words" target already
        # exceeded 60s at a normal narration pace with pauses, which is why
        # duration_15_to_60s kept failing in practice. The target must be
        # comfortably below 140.
        self.assertIn("110-130 words", creative_director._SYSTEM_PROMPT)
        self.assertNotIn("140-160 words", creative_director._SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
