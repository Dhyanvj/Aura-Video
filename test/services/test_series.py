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
from app.agents.schemas import CreativeBrief, MetadataDraft
from app.db import session_scope
from app.db.models import AgentEvent, ContentTypeTemplate, ProjectStatus, Series, VideoProject


def _brief_recommending(voice: str) -> CreativeBrief:
    return CreativeBrief(
        script="A short punchy script.",
        search_terms=["clip a"],
        music_direction="upbeat",
        bgm_file=None,
        voice_recommendation=voice,
        subtitle_style="bottom",
        metadata_draft=MetadataDraft(working_title="Title", hook_variants=["hook"]),
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
        os.remove(self._db_path)

    def test_five_built_in_templates_seeded_once(self):
        with session_scope() as session:
            ids = {t.id for t in session.exec(select(ContentTypeTemplate)).all()}
        self.assertEqual(ids, {"motivational", "fun_facts", "ai_news", "world_news", "trending_now"})

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


class TestSeriesBible(unittest.TestCase):
    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

    def tearDown(self):
        db_session.engine = self._original_engine
        os.remove(self._db_path)

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


if __name__ == "__main__":
    unittest.main()
