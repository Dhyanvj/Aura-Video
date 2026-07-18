import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlmodel import create_engine, select

import app.db.session as db_session
from app.agents import orchestrator
from app.agents.schemas import CreativeBrief, MetadataDraft, QuoteOrLesson
from app.db import session_scope
from app.db.models import AgentEvent, ContentTypeTemplate, ProjectStatus, Series, VideoProject


def _brief_recommending(voice: str, quote_or_lesson=None) -> CreativeBrief:
    return CreativeBrief(
        script="A short punchy script.",
        search_terms=["clip a"],
        music_direction="upbeat",
        bgm_file=None,
        voice_recommendation=voice,
        subtitle_style="bottom",
        metadata_draft=MetadataDraft(working_title="Title", hook_variants=["hook"]),
        quote_or_lesson=quote_or_lesson,
    )


class TestContentTypeSeeding(unittest.TestCase):
    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

    def tearDown(self):
        db_session.engine = self._original_engine
        # Not deleted: a still-running daemon thread from this test can
        # otherwise reconnect after deletion and silently recreate an
        # empty, tableless file at the same path, corrupting the next test.
        pass

    def test_built_in_templates_seeded_once(self):
        with session_scope() as session:
            ids = {t.id for t in session.exec(select(ContentTypeTemplate)).all()}
        self.assertEqual(
            ids,
            {
                "motivational_story",
                "motivational_quote",
                "motivational_speech",
                "motivational_words",
                "motivational_lines",
                "motivational",
                "fun_facts",
                "ai_news",
                "world_news",
                "trending_now",
            },
        )

    def test_seeding_does_not_overwrite_edited_rows(self):
        # A user-edited template must survive a restart (which re-runs seeding).
        with session_scope() as session:
            template = session.get(ContentTypeTemplate, "fun_facts")
            template.default_duration_s = 999
            session.add(template)
            session.commit()

        db_session.init_db()

        with session_scope() as session:
            template = session.get(ContentTypeTemplate, "fun_facts")
        self.assertEqual(template.default_duration_s, 999)

    def test_upgrade_migration_applies_motivational_rework_to_untouched_row(self):
        # Simulates a database from before the Part 2 rework: the row still
        # has the old built-in label, so the narrow, guarded migration must
        # bring it up to the new content on the next startup.
        with session_scope() as session:
            template = session.get(ContentTypeTemplate, "motivational")
            template.label = "Motivational"
            template.scriptcraft_overrides = {"structure": "story-arc", "cta_style": "woven-into-payoff"}
            session.add(template)
            session.commit()

        db_session.init_db()

        with session_scope() as session:
            template = session.get(ContentTypeTemplate, "motivational")
        self.assertEqual(template.label, "Motivational Quotes & Life Lessons")
        self.assertEqual(template.scriptcraft_overrides["structure"], "quote_or_lesson_centered")

    def test_upgrade_migration_skips_a_user_renamed_row(self):
        # If the user already renamed/customized the row, the migration must
        # not clobber their edit.
        with session_scope() as session:
            template = session.get(ContentTypeTemplate, "motivational")
            template.label = "My Custom Motivational Series"
            session.add(template)
            session.commit()

        db_session.init_db()

        with session_scope() as session:
            template = session.get(ContentTypeTemplate, "motivational")
        self.assertEqual(template.label, "My Custom Motivational Series")


class TestSeriesBible(unittest.TestCase):
    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

    def tearDown(self):
        db_session.engine = self._original_engine
        # Not deleted: a still-running daemon thread from this test can
        # otherwise reconnect after deletion and silently recreate an
        # empty, tableless file at the same path, corrupting the next test.
        pass

    def _create_project(self, series_id=None, episode_number=None) -> int:
        with session_scope() as session:
            project = VideoProject(
                status=ProjectStatus.SCRIPTING.value,
                topic="t",
                niche="n",
                series_id=series_id,
                episode_number=episode_number,
            )
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    def test_create_series_starts_with_no_locked_voice(self):
        series_id = orchestrator.create_series("motivational", "Stoic Mornings")
        with session_scope() as session:
            series = session.get(Series, series_id)
        self.assertEqual(series.voice_id, "")
        self.assertEqual(series.episode_counter, 0)

    def test_next_episode_number_increments(self):
        series_id = orchestrator.create_series("motivational", "Stoic Mornings")
        self.assertEqual(orchestrator.next_episode_number(series_id), 1)
        self.assertEqual(orchestrator.next_episode_number(series_id), 2)
        self.assertEqual(orchestrator.next_episode_number(series_id), 3)

    def test_next_episode_number_raises_for_unknown_series(self):
        with self.assertRaises(ValueError):
            orchestrator.next_episode_number(999999)

    def test_founding_episode_locks_series_voice(self):
        # The first episode's Creative Director recommendation becomes the
        # series' permanent voice - there's nothing to enforce yet.
        series_id = orchestrator.create_series("motivational", "Stoic Mornings")
        project_id = self._create_project(series_id=series_id, episode_number=1)
        brief = _brief_recommending("en-US-GuyNeural-Male")

        resolved = orchestrator._resolve_voice_name(project_id, brief)

        self.assertEqual(resolved, "en-US-GuyNeural-Male")
        with session_scope() as session:
            series = session.get(Series, series_id)
        self.assertEqual(series.voice_id, "en-US-GuyNeural-Male")

    def test_subsequent_episode_voice_is_hard_enforced(self):
        # Once a voice is locked, later episodes cannot drift even if the
        # Creative Director recommends a different, otherwise-valid voice.
        series_id = orchestrator.create_series("motivational", "Stoic Mornings")
        with session_scope() as session:
            series = session.get(Series, series_id)
            series.voice_id = "en-US-GuyNeural-Male"
            session.add(series)
            session.commit()

        project_id = self._create_project(series_id=series_id, episode_number=2)
        brief = _brief_recommending("en-US-AriaNeural-Female")

        resolved = orchestrator._resolve_voice_name(project_id, brief)

        self.assertEqual(resolved, "en-US-GuyNeural-Male")
        with session_scope() as session:
            events = session.exec(select(AgentEvent).where(AgentEvent.project_id == project_id)).all()
        self.assertTrue(any("Series voice enforced" in e.message for e in events))

    def test_non_series_project_is_unaffected(self):
        project_id = self._create_project()
        brief = _brief_recommending("en-US-GuyNeural-Male")
        resolved = orchestrator._resolve_voice_name(project_id, brief)
        self.assertEqual(resolved, "en-US-GuyNeural-Male")

    def test_write_brief_appends_rolling_summary_for_series_episode(self):
        series_id = orchestrator.create_series("fun_facts", "Ocean Facts")
        project_id = self._create_project(series_id=series_id, episode_number=1)

        with patch(
            "app.agents.creative_director.CreativeDirector.write",
            return_value=_brief_recommending("en-US-GuyNeural-Male"),
        ):
            orchestrator._write_brief(project_id, "Whales", "ocean", None)

        with session_scope() as session:
            series = session.get(Series, series_id)
        self.assertIn("Episode 1: Whales", series.rolling_summary)

    def test_write_brief_does_not_touch_rolling_summary_for_one_off_project(self):
        project_id = self._create_project()
        with patch(
            "app.agents.creative_director.CreativeDirector.write",
            return_value=_brief_recommending("en-US-GuyNeural-Male"),
        ):
            orchestrator._write_brief(project_id, "Whales", "ocean", None)
        # No series_id on the project - nothing to assert on a Series row,
        # this just confirms _write_brief doesn't raise without one.

    def test_rolling_summary_keeps_only_last_five_lines(self):
        series_id = orchestrator.create_series("fun_facts", "Ocean Facts")
        for n in range(1, 8):
            orchestrator._append_series_summary(series_id, n, f"topic {n}")
        with session_scope() as session:
            series = session.get(Series, series_id)
        lines = series.rolling_summary.split("\n")
        self.assertEqual(len(lines), 5)
        self.assertEqual(lines[0], "Episode 3: topic 3")
        self.assertEqual(lines[-1], "Episode 7: topic 7")


class TestQuoteOrLessonWiring(unittest.TestCase):
    """
    Part 2 (Motivational Quotes & Life Lessons): the project's content type
    must reach CreativeDirector.write(), and a quote/lesson centerpiece must
    reach VideoParams so the renderer knows what to give the on-screen
    treatment to.
    """

    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

    def tearDown(self):
        db_session.engine = self._original_engine
        # Not deleted: a still-running daemon thread from this test can
        # otherwise reconnect after deletion and silently recreate an
        # empty, tableless file at the same path, corrupting the next test.
        pass

    def _create_project(self, content_type_id=None) -> int:
        with session_scope() as session:
            project = VideoProject(
                status=ProjectStatus.SCRIPTING.value, topic="t", niche="n", content_type_id=content_type_id
            )
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    def test_write_brief_passes_project_content_type_id_to_creative_director(self):
        project_id = self._create_project(content_type_id="motivational")
        quote = QuoteOrLesson(is_quote=False, text="Keep the promise.", attribution=None)
        with patch(
            "app.agents.creative_director.CreativeDirector.write",
            return_value=_brief_recommending("en-US-GuyNeural-Male", quote_or_lesson=quote),
        ) as mock_write:
            orchestrator._write_brief(project_id, "discipline", "self-improvement", None)

        self.assertEqual(mock_write.call_args.kwargs["content_type_id"], "motivational")

    def test_video_params_populate_quote_text_and_attribution_for_a_quote(self):
        project_id = self._create_project(content_type_id="motivational")
        quote = QuoteOrLesson(is_quote=True, text="The obstacle is the way.", attribution="Ryan Holiday")
        brief = _brief_recommending("en-US-GuyNeural-Male", quote_or_lesson=quote)

        params = orchestrator._video_params_from_brief(project_id, "discipline", brief)

        self.assertEqual(params.quote_text, "The obstacle is the way.")
        self.assertEqual(params.quote_attribution, "Ryan Holiday")

    def test_video_params_omit_attribution_for_a_life_lesson(self):
        project_id = self._create_project(content_type_id="motivational")
        lesson = QuoteOrLesson(is_quote=False, text="Discipline is a private vote.", attribution=None)
        brief = _brief_recommending("en-US-GuyNeural-Male", quote_or_lesson=lesson)

        params = orchestrator._video_params_from_brief(project_id, "discipline", brief)

        self.assertEqual(params.quote_text, "Discipline is a private vote.")
        self.assertIsNone(params.quote_attribution)

    def test_video_params_have_no_quote_fields_without_a_centerpiece(self):
        project_id = self._create_project(content_type_id="fun_facts")
        brief = _brief_recommending("en-US-GuyNeural-Male", quote_or_lesson=None)

        params = orchestrator._video_params_from_brief(project_id, "ocean facts", brief)

        self.assertIsNone(params.quote_text)
        self.assertIsNone(params.quote_attribution)

    def test_video_params_enable_ai_image_fallback_when_content_type_allows_it(self):
        # docs/DECISIONS_V3.md §6: fun_facts' seeded visual_strategy has
        # ai_gen_allowed=True (app/db/seed.py).
        project_id = self._create_project(content_type_id="fun_facts")
        brief = _brief_recommending("en-US-GuyNeural-Male", quote_or_lesson=None)

        params = orchestrator._video_params_from_brief(project_id, "ocean facts", brief)

        self.assertTrue(params.ai_image_fallback_enabled)

    def test_video_params_disable_ai_image_fallback_when_content_type_forbids_it(self):
        # world_news' seeded visual_strategy has ai_gen_allowed=False.
        project_id = self._create_project(content_type_id="world_news")
        brief = _brief_recommending("en-US-GuyNeural-Male", quote_or_lesson=None)

        params = orchestrator._video_params_from_brief(project_id, "a story", brief)

        self.assertFalse(params.ai_image_fallback_enabled)

    def test_video_params_disable_ai_image_fallback_with_no_content_type(self):
        project_id = self._create_project(content_type_id=None)
        brief = _brief_recommending("en-US-GuyNeural-Male", quote_or_lesson=None)

        params = orchestrator._video_params_from_brief(project_id, "a topic", brief)

        self.assertFalse(params.ai_image_fallback_enabled)


if __name__ == "__main__":
    unittest.main()
