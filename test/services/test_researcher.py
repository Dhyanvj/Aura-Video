import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.agents.researcher import Researcher
from app.agents.schemas import ResearchDossier, SourceCitation


class TestResearcherPromptSelection(unittest.TestCase):
    """
    Part 3: each content type needs a distinct verification strategy (quote
    wording+attribution, myth-checked facts, freshness-windowed news) - this
    is the mechanism that makes that per-type behavior possible.
    """

    def test_motivational_prompt_requires_two_sources_and_attribution(self):
        researcher = Researcher(project_id=None)
        prompt = researcher._research_prompt("motivational", None)
        self.assertIn("quote", prompt.lower())
        self.assertIn("2 independent sources", prompt)

    def test_fun_facts_prompt_includes_myth_check(self):
        researcher = Researcher(project_id=None)
        prompt = researcher._research_prompt("fun_facts", None)
        self.assertIn("myth", prompt.lower())

    def test_news_prompt_bakes_in_freshness_window(self):
        researcher = Researcher(project_id=None)
        prompt = researcher._research_prompt("ai_news", 24)
        self.assertIn("last\n24 hours".replace("\n", " "), " ".join(prompt.split()))

    def test_world_news_uses_same_news_prompt_shape_as_ai_news(self):
        researcher = Researcher(project_id=None)
        self.assertEqual(researcher._research_prompt("world_news", 12), researcher._research_prompt("ai_news", 12))

    def test_unknown_content_type_falls_back_to_generic_prompt(self):
        researcher = Researcher(project_id=None)
        prompt = researcher._research_prompt("trending_now", None)
        self.assertIn("independent sources", prompt)


class TestResearcherResearch(unittest.TestCase):
    def test_failed_web_search_returns_reduced_verification_dossier_without_raising(self):
        researcher = Researcher(project_id=None)
        with patch.object(researcher, "call_with_web_search", return_value=("", [], False)):
            dossier = researcher.research(content_type_id="ai_news", topic_hint="a story", freshness_window_hours=24)

        self.assertTrue(dossier.reduced_verification)
        self.assertEqual(dossier.topic, "a story")
        self.assertEqual(dossier.freshness_window_hours, 24)

    def test_successful_web_search_is_structured_into_a_dossier(self):
        researcher = Researcher(project_id=None)
        structured = ResearchDossier(
            topic="Octopuses have three hearts",
            key_facts=[],
            sources=[SourceCitation(url="https://example.com", title="Example")],
        )
        with patch.object(
            researcher,
            "call_with_web_search",
            return_value=("octopuses have three hearts", [{"url": "https://example.com", "title": "Example"}], True),
        ), patch.object(researcher, "call_json", return_value=structured) as mock_structure:
            dossier = researcher.research(content_type_id="fun_facts", topic_hint="octopus facts")

        self.assertFalse(dossier.reduced_verification)
        self.assertEqual(dossier.topic, "Octopuses have three hearts")
        mock_structure.assert_called_once()

    def test_freshness_window_is_reapplied_after_structuring_even_if_model_omits_it(self):
        # _structure_dossier must reflect the constraint actually enforced on
        # this research pass, not whatever (if anything) the model echoed
        # back into the schema - a model that leaves it blank shouldn't erase
        # the freshness requirement that was actually applied.
        researcher = Researcher(project_id=None)
        structured = ResearchDossier(topic="a story", freshness_window_hours=None)
        with patch.object(
            researcher, "call_with_web_search", return_value=("a story happened", [], True)
        ), patch.object(researcher, "call_json", return_value=structured):
            dossier = researcher.research(content_type_id="ai_news", topic_hint="a story", freshness_window_hours=24)

        self.assertEqual(dossier.freshness_window_hours, 24)


if __name__ == "__main__":
    unittest.main()
