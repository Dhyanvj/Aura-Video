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
        os.remove(self._db_path)

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

    def test_list_content_types_returns_seeded_built_ins(self):
        response = self.client.get("/api/v1/content-types")
        self.assertEqual(response.status_code, 200)
        ids = {t["id"] for t in response.json()["data"]["content_types"]}
        self.assertEqual(ids, {"motivational", "fun_facts", "ai_news", "world_news", "trending_now"})

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
            "/api/v1/series", json={"content_type_id": "motivational", "title": "Stoic Mornings"}
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
        response = self.client.post("/api/v1/series", json={"content_type_id": "motivational", "title": "  "})
        self.assertEqual(response.status_code, 400)

    def test_get_series_includes_episode_list(self):
        series_id = self.client.post(
            "/api/v1/series", json={"content_type_id": "fun_facts", "title": "Ocean Facts"}
        ).json()["data"]["series_id"]

        with patch.object(agent_base, "is_configured", return_value=False):
            response = self.client.post(
                "/api/v1/projects",
                json={
                    "topic": "Whales",
                    "content_type_id": "fun_facts",
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
                    "content_type_id": "motivational",
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
            self.assertEqual(project.content_type_id, "motivational")
            self.assertEqual(project.quality_preset, "standard")
            self.assertEqual(project.episode_number, 1)
            self.assertIsNotNone(project.series_id)

    def test_create_project_new_series_without_title_returns_400(self):
        response = self.client.post(
            "/api/v1/projects",
            json={"topic": "x", "content_type_id": "motivational", "series_mode": "new"},
        )
        self.assertEqual(response.status_code, 400)

    def test_create_project_continue_series_increments_episode_number(self):
        series_id = self.client.post(
            "/api/v1/series", json={"content_type_id": "fun_facts", "title": "Ocean Facts"}
        ).json()["data"]["series_id"]

        with patch.object(agent_base, "is_configured", return_value=False):
            first = self.client.post(
                "/api/v1/projects",
                json={
                    "topic": "Whales",
                    "content_type_id": "fun_facts",
                    "series_mode": "continue",
                    "series_id": series_id,
                },
            )
            second = self.client.post(
                "/api/v1/projects",
                json={
                    "topic": "Coral",
                    "content_type_id": "fun_facts",
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
            json={"topic": "x", "content_type_id": "fun_facts", "series_mode": "continue"},
        )
        self.assertEqual(response.status_code, 400)

    def test_create_project_continue_unknown_series_returns_404(self):
        response = self.client.post(
            "/api/v1/projects",
            json={"topic": "x", "content_type_id": "fun_facts", "series_mode": "continue", "series_id": 999999},
        )
        self.assertEqual(response.status_code, 404)

    def test_create_project_unknown_content_type_returns_404(self):
        response = self.client.post("/api/v1/projects", json={"topic": "x", "content_type_id": "does-not-exist"})
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
