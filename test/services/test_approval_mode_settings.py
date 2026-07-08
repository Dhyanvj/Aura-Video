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
from app.agents import orchestrator
from app.config import config
from app.db import session_scope
from app.db.models import VideoProject
from test.services._test_helpers import IsolatedStorageDirMixin


class _BaseSettingsTest(IsolatedStorageDirMixin, unittest.TestCase):
    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()
        self._start_isolated_storage_dir()
        self._original_agents = dict(config.agents)

    def tearDown(self):
        config.agents.clear()
        config.agents.update(self._original_agents)
        db_session.engine = self._original_engine
        self._stop_isolated_storage_dir()

    def _get_project(self, project_id: int) -> VideoProject:
        with session_scope() as session:
            return session.get(VideoProject, project_id)


class TestApprovalModeMigrationFromAutopilotLevel(_BaseSettingsTest):
    """
    docs/DECISIONS_V3.md: config.agents.autopilot_level ("manual"/"semi")
    was stored and shown in Settings but never actually enforced anywhere in
    the pipeline. orchestrator._resolve_approval_mode migrates it into the
    new approval_mode setting: "manual" -> "manual" (both meant "approve
    topic/script too"), "semi" -> "automatic" (both meant "only the final
    video needs a human"). approval_mode, once set, always wins.
    """

    def test_defaults_to_manual_when_nothing_is_configured(self):
        config.agents.pop("approval_mode", None)
        config.agents.pop("autopilot_level", None)
        self.assertEqual(orchestrator._resolve_approval_mode(), "manual")

    def test_legacy_semi_maps_to_automatic(self):
        config.agents.pop("approval_mode", None)
        config.agents["autopilot_level"] = "semi"
        self.assertEqual(orchestrator._resolve_approval_mode(), "automatic")

    def test_legacy_manual_maps_to_manual(self):
        config.agents.pop("approval_mode", None)
        config.agents["autopilot_level"] = "manual"
        self.assertEqual(orchestrator._resolve_approval_mode(), "manual")

    def test_explicit_approval_mode_wins_over_legacy_autopilot_level(self):
        config.agents["autopilot_level"] = "semi"
        config.agents["approval_mode"] = "manual"
        self.assertEqual(orchestrator._resolve_approval_mode(), "manual")

    def test_override_param_wins_over_everything(self):
        config.agents["approval_mode"] = "manual"
        self.assertEqual(orchestrator._resolve_approval_mode("automatic"), "automatic")


class TestSettingsApiExposesApprovalMode(_BaseSettingsTest):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient

        from app.asgi import app

        self.client = TestClient(app)

    def test_get_settings_reflects_legacy_migration(self):
        config.agents.pop("approval_mode", None)
        config.agents["autopilot_level"] = "semi"
        response = self.client.get("/api/v1/settings")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["approval_mode"], "automatic")

    def test_put_settings_updates_approval_mode(self):
        with patch("app.services.scheduler.start_scheduler"), patch("app.services.scheduler.stop_scheduler"):
            response = self.client.put("/api/v1/settings", json={"approval_mode": "automatic"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["approval_mode"], "automatic")
        self.assertEqual(config.agents["approval_mode"], "automatic")

    def test_put_settings_rejects_invalid_approval_mode(self):
        response = self.client.put("/api/v1/settings", json={"approval_mode": "semi-auto-typo"})
        self.assertEqual(response.status_code, 400)


class TestPerProjectApprovalModeOverride(_BaseSettingsTest):
    def test_override_applies_only_to_the_overridden_project(self):
        config.agents["approval_mode"] = "manual"

        # is_configured=False makes the background pipeline thread fail fast
        # and deterministically instead of hitting the real Anthropic API
        # with this repo's live config.toml key - these tests only care
        # about the approval_mode field set at creation, before that thread
        # ever starts.
        with patch.object(agent_base, "is_configured", return_value=False):
            default_id = orchestrator.start_manual_project(topic="default mode", niche="n")
            overridden_id = orchestrator.start_manual_project(
                topic="overridden mode", niche="n", approval_mode_override="automatic"
            )

        self.assertEqual(self._get_project(default_id).approval_mode, "manual")
        self.assertEqual(self._get_project(overridden_id).approval_mode, "automatic")

    def test_in_flight_project_keeps_its_mode_after_settings_change(self):
        config.agents["approval_mode"] = "manual"
        with patch.object(agent_base, "is_configured", return_value=False):
            project_id = orchestrator.start_manual_project(topic="t", niche="n")
        self.assertEqual(self._get_project(project_id).approval_mode, "manual")

        # A later Settings change must never affect an already-created project.
        config.agents["approval_mode"] = "automatic"
        self.assertEqual(self._get_project(project_id).approval_mode, "manual")
        self.assertEqual(orchestrator._project_approval_mode(project_id), "manual")


if __name__ == "__main__":
    unittest.main()
