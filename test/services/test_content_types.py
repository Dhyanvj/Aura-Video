import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlmodel import create_engine

import app.db.session as db_session
from app.agents import base as agent_base
from app.db import session_scope
from app.db.models import ProjectStatus, VideoProject


class TestContentTypeAndSeriesEndpoints(unittest.TestCase):
    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

        from fastapi.testclient import TestClient

        from app.asgi import app

        self.client = TestClient(app)

    def tearDown(self):
        db_session.engine = self._original_engine
        # Not deleted: a still-running daemon thread from this test can
        # otherwise reconnect after deletion and silently recreate an
        # empty, tableless file at the same path, corrupting the next test.
        pass

    def _wait_for_failed(self, project_id: int, timeout: float = 10.0) -> None:
        # Project creation returns as soon as the row is written, but the
        # pipeline keeps running in a background thread. With
        # agent_base.is_configured patched to False it fails almost
        # instantly - waiting for that (instead of leaving the thread
        # dangling past tearDown) keeps these tests from racing the next
        # test's fresh temp database.
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            with session_scope() as session:
                if session.get(VideoProject, project_id).status == ProjectStatus.FAILED.value:
                    return
            time.sleep(0.05)
        self.fail(f"project {project_id} never reached FAILED")

    # The 5 motivational sub-formats plus the 5 disabled legacy types (kept,
    # never deleted, per the motivational-only pivot).
    _ENABLED_IDS = {
        "motivational_story",
        "motivational_quote",
        "motivational_speech",
        "motivational_words",
        "motivational_lines",
    }
    _DISABLED_IDS = {"motivational", "fun_facts", "ai_news", "world_news", "trending_now"}

    def test_list_content_types_returns_seeded_built_ins(self):
        response = self.client.get("/api/v1/content-types")
        self.assertEqual(response.status_code, 200)
        types = response.json()["data"]["content_types"]
        ids = {t["id"] for t in types}
        self.assertEqual(ids, self._ENABLED_IDS | self._DISABLED_IDS)
        # Every built-in type must have a non-empty description for the New
        # Video card copy.
        for t in types:
            self.assertTrue(t["description"], msg=f"{t['id']} has no description")

    def test_motivational_only_pivot_enables_five_sub_formats_and_disables_the_rest(self):
        # Focus decision: the platform now only creates the 5 motivational
        # sub-formats. Everything else (including the old umbrella
        # "motivational" row) is disabled, not deleted, and re-enableable
        # from Settings with no code change.
        response = self.client.get("/api/v1/content-types")
        by_id = {t["id"]: t for t in response.json()["data"]["content_types"]}
        for enabled_id in self._ENABLED_IDS:
            self.assertTrue(by_id[enabled_id]["enabled"], msg=f"{enabled_id} should be enabled")
        for disabled_id in self._DISABLED_IDS:
            self.assertFalse(by_id[disabled_id]["enabled"], msg=f"{disabled_id} should be disabled")

    def test_enabled_only_query_hides_disabled_types(self):
        response = self.client.get("/api/v1/content-types", params={"enabled_only": "true"})
        ids = {t["id"] for t in response.json()["data"]["content_types"]}
        self.assertEqual(ids, self._ENABLED_IDS)

    def test_motivational_quote_template_reflects_the_quote_lesson_format(self):
        # motivational_quote is the direct successor to the old umbrella
        # "motivational" row's quote/lesson-centered format.
        response = self.client.get("/api/v1/content-types")
        quote = next(t for t in response.json()["data"]["content_types"] if t["id"] == "motivational_quote")
        self.assertEqual(quote["label"], "Motivational Quote")
        self.assertEqual(quote["scriptcraft_overrides"]["structure"], "quote_or_lesson_centered")
        self.assertTrue(quote["enabled"])

    def test_reenabling_a_content_type_survives_a_reseed(self):
        # A human re-enabling a legacy type from Settings must never be
        # silently undone by a later app restart re-running seed_content_types().
        from app.db.seed import seed_content_types

        response = self.client.put("/api/v1/content-types/fun_facts", json={"enabled": True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["enabled"])

        with session_scope() as session:
            seed_content_types(session)

        response = self.client.get("/api/v1/content-types")
        fun_facts = next(t for t in response.json()["data"]["content_types"] if t["id"] == "fun_facts")
        self.assertTrue(fun_facts["enabled"])

    def test_ai_news_template_has_energetic_tech_tone(self):
        # voice_style/music_palette went from dead metadata to fields that
        # actually drive TTS voice choice and BGM selection (orchestrator.py)
        # - AI News should read as an energetic tech-news presenter, not the
        # old "neutral, enthusiastic" / "tech_ambient" placeholder.
        response = self.client.get("/api/v1/content-types")
        ai_news = next(t for t in response.json()["data"]["content_types"] if t["id"] == "ai_news")
        self.assertIn("confident, energetic", ai_news["voice_style"])
        self.assertEqual(ai_news["music_palette"], "tech_energetic")

    def test_ai_news_tone_migration_upgrades_old_defaults_but_preserves_user_edits(self):
        from app.db.models import ContentTypeTemplate
        from app.db.seed import seed_content_types

        with session_scope() as session:
            template = session.get(ContentTypeTemplate, "ai_news")
            template.voice_style = "neutral, enthusiastic"
            template.music_palette = "tech_ambient"
            session.add(template)
            session.commit()

        with session_scope() as session:
            seed_content_types(session)

        with session_scope() as session:
            template = session.get(ContentTypeTemplate, "ai_news")
            self.assertIn("confident, energetic", template.voice_style)
            self.assertEqual(template.music_palette, "tech_energetic")

            # A user's own edit (even one that happens to look old-default-ish
            # on only one of the two fields) must never be clobbered by a
            # later seed_content_types() call.
            template.voice_style = "a custom voice style the user wrote"
            session.add(template)
            session.commit()

        with session_scope() as session:
            seed_content_types(session)

        with session_scope() as session:
            template = session.get(ContentTypeTemplate, "ai_news")
            self.assertEqual(template.voice_style, "a custom voice style the user wrote")

    def test_update_content_type_persists_description(self):
        response = self.client.put(
            "/api/v1/content-types/fun_facts", json={"description": "A custom description."}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["description"], "A custom description.")

    def test_update_content_type_persists_editable_fields(self):
        response = self.client.put(
            "/api/v1/content-types/fun_facts",
            json={
                "default_duration_s": 55,
                "research_required": True,
                "visual_strategy": {"ai_gen_allowed": False},
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["default_duration_s"], 55)
        self.assertTrue(data["research_required"])
        self.assertEqual(data["visual_strategy"], {"ai_gen_allowed": False})

        # And it's actually persisted, not just echoed back.
        response = self.client.get("/api/v1/content-types")
        updated = next(t for t in response.json()["data"]["content_types"] if t["id"] == "fun_facts")
        self.assertEqual(updated["default_duration_s"], 55)

    def test_update_unknown_content_type_returns_404(self):
        response = self.client.put("/api/v1/content-types/does-not-exist", json={"label": "x"})
        self.assertEqual(response.status_code, 404)

    def test_create_and_list_series(self):
        response = self.client.post(
            "/api/v1/series", json={"content_type_id": "motivational_quote", "title": "Stoic Mornings"}
        )
        self.assertEqual(response.status_code, 200)
        series_id = response.json()["data"]["series_id"]

        response = self.client.get("/api/v1/series")
        self.assertEqual(response.status_code, 200)
        series_list = response.json()["data"]["series"]
        self.assertEqual(len(series_list), 1)
        self.assertEqual(series_list[0]["id"], series_id)
        self.assertEqual(series_list[0]["voice_id"], "")
        self.assertEqual(series_list[0]["episode_count"], 0)

    def test_create_series_with_unknown_content_type_returns_404(self):
        response = self.client.post("/api/v1/series", json={"content_type_id": "nope", "title": "x"})
        self.assertEqual(response.status_code, 404)

    def test_create_series_with_blank_title_returns_400(self):
        response = self.client.post(
            "/api/v1/series", json={"content_type_id": "motivational_quote", "title": "  "}
        )
        self.assertEqual(response.status_code, 400)

    def test_create_series_with_disabled_content_type_returns_400(self):
        response = self.client.post(
            "/api/v1/series", json={"content_type_id": "fun_facts", "title": "Ocean Facts"}
        )
        self.assertEqual(response.status_code, 400)

    def test_get_series_includes_episode_list(self):
        series_id = self.client.post(
            "/api/v1/series", json={"content_type_id": "motivational_quote", "title": "Ocean Facts"}
        ).json()["data"]["series_id"]

        with patch.object(agent_base, "is_configured", return_value=False):
            response = self.client.post(
                "/api/v1/projects",
                json={
                    "topic": "Whales",
                    "content_type_id": "motivational_quote",
                    "series_mode": "continue",
                    "series_id": series_id,
                },
            )
            self._wait_for_failed(response.json()["data"]["project_id"])

        response = self.client.get(f"/api/v1/series/{series_id}")
        self.assertEqual(response.status_code, 200)
        episodes = response.json()["data"]["episodes"]
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["episode_number"], 1)
        self.assertEqual(episodes[0]["topic"], "Whales")

    def test_create_project_new_series_sets_episode_one_and_content_type(self):
        with patch.object(agent_base, "is_configured", return_value=False):
            response = self.client.post(
                "/api/v1/projects",
                json={
                    "topic": "Morning routines",
                    "content_type_id": "motivational_quote",
                    "quality_preset": "standard",
                    "series_mode": "new",
                    "series_title": "Stoic Mornings",
                },
            )
            self.assertEqual(response.status_code, 200)
            project_id = response.json()["data"]["project_id"]
            self._wait_for_failed(project_id)

        with session_scope() as session:
            project = session.get(VideoProject, project_id)
            self.assertEqual(project.content_type_id, "motivational_quote")
            self.assertEqual(project.quality_preset, "standard")
            self.assertEqual(project.episode_number, 1)
            self.assertIsNotNone(project.series_id)

    def test_create_project_new_series_without_title_returns_400(self):
        response = self.client.post(
            "/api/v1/projects",
            json={"topic": "x", "content_type_id": "motivational_quote", "series_mode": "new"},
        )
        self.assertEqual(response.status_code, 400)

    def test_create_project_new_series_without_content_type_returns_400(self):
        response = self.client.post(
            "/api/v1/projects",
            json={"topic": "x", "series_mode": "new", "series_title": "Untitled"},
        )
        self.assertEqual(response.status_code, 400)

    def test_create_project_continue_series_increments_episode_number(self):
        series_id = self.client.post(
            "/api/v1/series", json={"content_type_id": "motivational_quote", "title": "Ocean Facts"}
        ).json()["data"]["series_id"]

        with patch.object(agent_base, "is_configured", return_value=False):
            first = self.client.post(
                "/api/v1/projects",
                json={
                    "topic": "Whales",
                    "content_type_id": "motivational_quote",
                    "series_mode": "continue",
                    "series_id": series_id,
                },
            )
            second = self.client.post(
                "/api/v1/projects",
                json={
                    "topic": "Coral",
                    "content_type_id": "motivational_quote",
                    "series_mode": "continue",
                    "series_id": series_id,
                },
            )
            self._wait_for_failed(first.json()["data"]["project_id"])
            self._wait_for_failed(second.json()["data"]["project_id"])

        with session_scope() as session:
            first_project = session.get(VideoProject, first.json()["data"]["project_id"])
            second_project = session.get(VideoProject, second.json()["data"]["project_id"])
        self.assertEqual(first_project.episode_number, 1)
        self.assertEqual(second_project.episode_number, 2)
        self.assertEqual(first_project.series_id, series_id)
        self.assertEqual(second_project.series_id, series_id)

    def test_create_project_continue_series_missing_id_returns_400(self):
        response = self.client.post(
            "/api/v1/projects",
            json={"topic": "x", "content_type_id": "motivational_quote", "series_mode": "continue"},
        )
        self.assertEqual(response.status_code, 400)

    def test_create_project_continue_unknown_series_returns_404(self):
        response = self.client.post(
            "/api/v1/projects",
            json={
                "topic": "x",
                "content_type_id": "motivational_quote",
                "series_mode": "continue",
                "series_id": 999999,
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_create_project_unknown_content_type_returns_404(self):
        response = self.client.post("/api/v1/projects", json={"topic": "x", "content_type_id": "does-not-exist"})
        self.assertEqual(response.status_code, 404)

    def test_create_project_with_disabled_content_type_returns_400(self):
        response = self.client.post("/api/v1/projects", json={"topic": "x", "content_type_id": "fun_facts"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
