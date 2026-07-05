import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.agents import creative_director
from app.agents.creative_director import CreativeDirector
from app.agents.schemas import (
    CreativeBrief,
    KeyFact,
    MetadataDraft,
    QuoteOrLesson,
    ResearchDossier,
    SearchTermsRevision,
)


def _fake_brief(quote_or_lesson=None) -> CreativeBrief:
    return CreativeBrief(
        script="A short punchy script.",
        search_terms=["clip a"],
        music_direction="calm",
        bgm_file=None,
        voice_recommendation="en-US-GuyNeural-Male",
        subtitle_style="bottom",
        metadata_draft=MetadataDraft(working_title="t", hook_variants=[]),
        quote_or_lesson=quote_or_lesson,
    )


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


class TestCreativeDirectorContentTypeStructure(unittest.TestCase):
    """
    Part 2: Motivational Quotes & Life Lessons needs a distinct script
    structure (quote/lesson-centered) that other content types don't have
    yet - this is the mechanism that makes that per-type behavior possible.
    """

    def test_write_appends_motivational_addendum_for_motivational_content_type(self):
        director = CreativeDirector(project_id=None)
        quote = QuoteOrLesson(is_quote=True, text="The obstacle is the way.", attribution="Ryan Holiday")
        with patch.object(director, "call_json", return_value=_fake_brief(quote_or_lesson=quote)) as mock_call:
            director.write(topic="discipline", niche="self-improvement", content_type_id="motivational")

        _, kwargs = mock_call.call_args
        self.assertIn("Motivational Quotes & Life Lessons", kwargs["system"])
        self.assertIn("quote_or_lesson", kwargs["system"])

    def test_write_does_not_append_addendum_for_other_content_types(self):
        director = CreativeDirector(project_id=None)
        with patch.object(director, "call_json", return_value=_fake_brief()) as mock_call:
            director.write(topic="whales", niche="ocean", content_type_id="fun_facts")

        _, kwargs = mock_call.call_args
        self.assertNotIn("Motivational Quotes & Life Lessons", kwargs["system"])

    def test_write_does_not_require_addendum_content_for_none_content_type(self):
        director = CreativeDirector(project_id=None)
        with patch.object(director, "call_json", return_value=_fake_brief()):
            # Must not raise even though quote_or_lesson is None - only
            # content types in _REQUIRES_QUOTE_OR_LESSON need it populated.
            brief = director.write(topic="whales", niche="ocean", content_type_id=None)
        self.assertIsNone(brief.quote_or_lesson)

    def test_write_raises_when_motivational_brief_is_missing_quote_or_lesson(self):
        director = CreativeDirector(project_id=None)
        with patch.object(director, "call_json", return_value=_fake_brief(quote_or_lesson=None)):
            with self.assertRaises(ValueError):
                director.write(topic="discipline", niche="self-improvement", content_type_id="motivational")

    def test_write_succeeds_when_motivational_brief_has_quote_or_lesson(self):
        director = CreativeDirector(project_id=None)
        lesson = QuoteOrLesson(is_quote=False, text="Discipline is a private vote for who you want to become.")
        with patch.object(director, "call_json", return_value=_fake_brief(quote_or_lesson=lesson)):
            brief = director.write(topic="discipline", niche="self-improvement", content_type_id="motivational")
        self.assertEqual(brief.quote_or_lesson.is_quote, False)


class TestCreativeDirectorResearchDossier(unittest.TestCase):
    """
    Part 3: a verified Researcher dossier, when present, must actually reach
    the model and be called out as the sole source of truth for facts/quotes
    - otherwise "research_required" content types get no benefit from the
    Researcher having run at all.
    """

    def test_write_includes_dossier_in_payload_and_grounding_instruction(self):
        director = CreativeDirector(project_id=None)
        dossier = ResearchDossier(
            topic="Octopuses have three hearts",
            key_facts=[KeyFact(statement="Octopuses have three hearts", confidence="verified")],
        )
        with patch.object(director, "call_json", return_value=_fake_brief()) as mock_call:
            director.write(topic="octopus facts", niche="ocean", research_dossier=dossier)

        _, kwargs = mock_call.call_args
        self.assertIn("verified_research", kwargs["user"])
        self.assertIn("Octopuses have three hearts", kwargs["user"])
        self.assertIn("already verified", kwargs["system"])
        self.assertIn("do not introduce a new unverified fact", kwargs["system"])

    def test_write_without_dossier_omits_grounding_instruction(self):
        director = CreativeDirector(project_id=None)
        with patch.object(director, "call_json", return_value=_fake_brief()) as mock_call:
            director.write(topic="octopus facts", niche="ocean")

        _, kwargs = mock_call.call_args
        self.assertNotIn("verified_research", kwargs["user"])
        self.assertNotIn("already verified", kwargs["system"])


class TestCreativeDirectorHookVariety(unittest.TestCase):
    """
    docs/DECISIONS_V3.md §2: the Creative Director is told which hook
    patterns this content type has used recently, so it doesn't repeat the
    same opening technique back-to-back.
    """

    def test_recent_hook_patterns_are_injected_into_the_system_prompt(self):
        director = CreativeDirector(project_id=None)
        with patch.object(director, "call_json", return_value=_fake_brief()) as mock_call:
            director.write(
                topic="whales", niche="ocean", recent_hook_patterns=["question", "bold_claim", "question"]
            )

        _, kwargs = mock_call.call_args
        normalized_system = " ".join(kwargs["system"].split())
        self.assertIn("question, bold_claim, question", normalized_system)
        self.assertIn("DIFFERENT pattern", normalized_system)

    def test_no_recent_patterns_omits_the_variety_instruction(self):
        director = CreativeDirector(project_id=None)
        with patch.object(director, "call_json", return_value=_fake_brief()) as mock_call:
            director.write(topic="whales", niche="ocean", recent_hook_patterns=None)

        _, kwargs = mock_call.call_args
        self.assertNotIn("DIFFERENT pattern", kwargs["system"])

    def test_brief_carries_hook_pattern_and_opening_line_through(self):
        director = CreativeDirector(project_id=None)
        brief_with_hook = _fake_brief()
        brief_with_hook = brief_with_hook.model_copy(
            update={"hook_pattern": "statistic", "opening_line": "9 out of 10 people get this wrong."}
        )
        with patch.object(director, "call_json", return_value=brief_with_hook):
            brief = director.write(topic="whales", niche="ocean")

        self.assertEqual(brief.hook_pattern, "statistic")
        self.assertEqual(brief.opening_line, "9 out of 10 people get this wrong.")


if __name__ == "__main__":
    unittest.main()
