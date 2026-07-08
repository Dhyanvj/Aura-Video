import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlmodel import create_engine, select

import app.db.session as db_session
from app.agents import base as agent_base
from app.agents import orchestrator
from app.agents.schemas import CreativeBrief, MetadataDraft
from app.config import config
from app.db import session_scope
from app.db.models import AgentEvent, ProjectStatus, VideoProject
from test.services._test_helpers import IsolatedStorageDirMixin


def _fake_brief(script: str = "A short punchy script.") -> CreativeBrief:
    return CreativeBrief(
        script=script,
        search_terms=["clip a", "clip b"],
        music_direction="upbeat",
        bgm_file=None,
        voice_recommendation="en-US-GuyNeural-Male",
        subtitle_style="bottom, bold",
        metadata_draft=MetadataDraft(working_title="Title", hook_variants=["hook"]),
    )


class _BaseGateTest(IsolatedStorageDirMixin, unittest.TestCase):
    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()
        self._start_isolated_storage_dir()
        self._original_approval_mode = config.agents.get("approval_mode")
        self._original_max_regen = config.agents.get("max_script_regenerations")

    def tearDown(self):
        if self._original_approval_mode is None:
            config.agents.pop("approval_mode", None)
        else:
            config.agents["approval_mode"] = self._original_approval_mode
        if self._original_max_regen is None:
            config.agents.pop("max_script_regenerations", None)
        else:
            config.agents["max_script_regenerations"] = self._original_max_regen
        db_session.engine = self._original_engine
        self._stop_isolated_storage_dir()

    def _get_project(self, project_id: int) -> VideoProject:
        with session_scope() as session:
            return session.get(VideoProject, project_id)

    def _create_project(self, status: ProjectStatus = ProjectStatus.AWAITING_SCRIPT_APPROVAL, **fields) -> int:
        with session_scope() as session:
            project = VideoProject(status=status.value, **fields)
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    def _events(self, project_id: int) -> list:
        with session_scope() as session:
            return session.exec(select(AgentEvent).where(AgentEvent.project_id == project_id)).all()

    def _wait_for_status(self, project_id: int, terminal_statuses: set, timeout: float = 30.0):
        import time

        deadline = time.time() + timeout
        status = None
        while time.time() < deadline:
            status = self._get_project(project_id).status
            if status in terminal_statuses:
                return status
            time.sleep(0.1)
        self.fail(f"project {project_id} never reached {terminal_statuses}, stuck at {status}")


class TestManualModeGateBlocksProduction(_BaseGateTest):
    def test_manual_mode_stops_at_awaiting_script_approval_with_zero_production_spend(self):
        config.agents["approval_mode"] = "manual"
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch("app.agents.producer.task_service.start") as mock_start:
            project_id = orchestrator.start_manual_project(topic="a topic", niche="a niche")
            self._wait_for_status(
                project_id, {ProjectStatus.AWAITING_SCRIPT_APPROVAL.value, ProjectStatus.FAILED.value}
            )

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.AWAITING_SCRIPT_APPROVAL.value)
        self.assertEqual(project.approval_mode, "manual")
        mock_start.assert_not_called()  # zero production spend before approval
        self.assertTrue(any("Awaiting human script approval" in e.message for e in self._events(project_id)))

    def test_approving_the_script_starts_production_and_reaches_final_review(self):
        config.agents["approval_mode"] = "manual"
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch("app.agents.producer.task_service.start") as mock_start, patch(
            "app.agents.base.BaseAgent.call_json_with_content"
        ) as mock_vision, patch(
            "app.agents.publisher.Publisher.prepare",
            return_value={"title_options": ["a", "b", "c"], "thumbnail_candidates": []},
        ):
            from app.agents.schemas import FrameFinding, VisionReview

            mock_vision.return_value = VisionReview(
                overall="pass", frame_findings=[FrameFinding(frame_index=0, matches_script=True, notes="ok")]
            )

            def fake_render(task_id, params, stop_at="video"):
                import subprocess

                video_path = os.path.join(tempfile.gettempdir(), f"{task_id}.mp4")
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=24:duration=20",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video_path,
                    ],
                    capture_output=True, timeout=60,
                )
                self.addCleanup(lambda: os.path.exists(video_path) and os.remove(video_path))
                from app.models import const
                from app.services import state as sm

                sm.state.update_task(task_id, state=const.TASK_STATE_COMPLETE, progress=100, videos=[video_path])

            mock_start.side_effect = fake_render

            project_id = orchestrator.start_manual_project(topic="a topic", niche="a niche")
            self._wait_for_status(project_id, {ProjectStatus.AWAITING_SCRIPT_APPROVAL.value, ProjectStatus.FAILED.value})
            self.assertEqual(self._get_project(project_id).status, ProjectStatus.AWAITING_SCRIPT_APPROVAL.value)
            mock_start.assert_not_called()

            orchestrator.approve_script(project_id)
            self._wait_for_status(
                project_id, {ProjectStatus.AWAITING_HUMAN_APPROVAL.value, ProjectStatus.FAILED.value}
            )

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.AWAITING_HUMAN_APPROVAL.value)
        mock_start.assert_called_once()
        self.assertTrue(any("Script approved by human" in e.message for e in self._events(project_id)))


class TestAutomaticModeAutoApproves(_BaseGateTest):
    def test_automatic_mode_logs_auto_approval_and_reaches_final_review(self):
        config.agents["approval_mode"] = "automatic"
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch("app.agents.producer.task_service.start") as mock_start, patch(
            "app.agents.base.BaseAgent.call_json_with_content"
        ) as mock_vision, patch(
            "app.agents.publisher.Publisher.prepare",
            return_value={"title_options": ["a", "b", "c"], "thumbnail_candidates": []},
        ):
            from app.agents.schemas import FrameFinding, VisionReview
            from app.models import const
            from app.services import state as sm

            mock_vision.return_value = VisionReview(
                overall="pass", frame_findings=[FrameFinding(frame_index=0, matches_script=True, notes="ok")]
            )

            def fake_render(task_id, params, stop_at="video"):
                sm.state.update_task(
                    task_id, state=const.TASK_STATE_COMPLETE, progress=100, videos=["/tmp/does-not-exist.mp4"]
                )

            mock_start.side_effect = fake_render

            project_id = orchestrator.start_manual_project(topic="a topic", niche="a niche")
            self._wait_for_status(project_id, {ProjectStatus.AWAITING_HUMAN_APPROVAL.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.approval_mode, "automatic")
        # Never paused at the gate on the way through.
        events = self._events(project_id)
        self.assertTrue(any("Script auto-approved by mode setting" in e.message for e in events))
        self.assertFalse(any("Awaiting human script approval" in e.message for e in events))


class TestApproveScriptEnforcement(_BaseGateTest):
    def test_raises_for_nonexistent_project(self):
        with self.assertRaises(ValueError):
            orchestrator.approve_script(999999)

    def test_raises_when_not_awaiting_script_approval(self):
        for status in (ProjectStatus.SCRIPTING, ProjectStatus.PRODUCING, ProjectStatus.AWAITING_HUMAN_APPROVAL):
            project_id = self._create_project(status)
            with self.assertRaises(PermissionError):
                orchestrator.approve_script(project_id)

    def test_raises_when_no_script_to_approve(self):
        project_id = self._create_project(ProjectStatus.AWAITING_SCRIPT_APPROVAL, brief=None)
        with self.assertRaises(RuntimeError):
            orchestrator.approve_script(project_id)


class TestRegenerateScript(_BaseGateTest):
    def test_regenerate_rewrites_script_and_returns_to_awaiting_approval(self):
        project_id = self._create_project(
            ProjectStatus.AWAITING_SCRIPT_APPROVAL,
            topic="a topic", niche="a niche", brief=_fake_brief("original script").model_dump(),
        )
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief("regenerated script")
        ):
            orchestrator.regenerate_script(project_id, "make it punchier")
            self._wait_for_status(
                project_id, {ProjectStatus.AWAITING_SCRIPT_APPROVAL.value, ProjectStatus.FAILED.value}
            )

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.AWAITING_SCRIPT_APPROVAL.value)
        self.assertEqual(project.brief["script"], "regenerated script")
        self.assertEqual(project.script_revision_count, 1)
        # Never touches the QA revision budget.
        self.assertEqual(project.revision_count, 0)

    def test_regenerate_caps_at_max_script_regenerations(self):
        config.agents["max_script_regenerations"] = 2
        project_id = self._create_project(
            ProjectStatus.AWAITING_SCRIPT_APPROVAL,
            topic="a topic", niche="a niche", brief=_fake_brief().model_dump(), script_revision_count=2,
        )
        with self.assertRaises(PermissionError):
            orchestrator.regenerate_script(project_id, "one more try")

    def test_regenerate_requires_awaiting_script_approval(self):
        project_id = self._create_project(ProjectStatus.PRODUCING)
        with self.assertRaises(PermissionError):
            orchestrator.regenerate_script(project_id, "notes")


class TestRejectTopicAtScript(_BaseGateTest):
    def test_reject_topic_returns_to_idea_stage_and_archives_script_under_revisions(self):
        project_id = self._create_project(
            ProjectStatus.AWAITING_SCRIPT_APPROVAL,
            topic="a bad topic", niche="a niche", content_type_id="fun_facts",
            brief=_fake_brief("the old script").model_dump(),
        )

        orchestrator.reject_topic_at_script(project_id, "this angle isn't working")

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.IDEA_PENDING.value)
        self.assertIsNone(project.topic)
        self.assertIsNone(project.brief)

        from app.services import project_storage
        from app.utils import utils

        abs_dir = os.path.join(utils.storage_dir(), project.storage_path)
        revisions_dir = os.path.join(abs_dir, "revisions")
        archived_scripts = [
            os.path.join(root, f) for root, _dirs, files in os.walk(revisions_dir) for f in files if f == "script.md"
        ]
        self.assertEqual(len(archived_scripts), 1)
        with open(archived_scripts[0], encoding="utf-8") as fh:
            self.assertIn("the old script", fh.read())

    def test_reject_topic_requires_awaiting_script_approval(self):
        project_id = self._create_project(ProjectStatus.PRODUCING)
        with self.assertRaises(PermissionError):
            orchestrator.reject_topic_at_script(project_id)


class TestResyncScenePlan(_BaseGateTest):
    def test_edit_resyncs_search_terms_without_rewriting_the_script(self):
        project_id = self._create_project(
            ProjectStatus.AWAITING_SCRIPT_APPROVAL,
            topic="a topic", niche="a niche",
            brief=_fake_brief("a human-edited script").model_dump(),
        )
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.revise_search_terms", return_value=["new term a", "new term b"]
        ) as mock_revise, patch("app.agents.creative_director.CreativeDirector.write") as mock_write:
            orchestrator.resync_scene_plan(project_id)

            import time

            deadline = time.time() + 10
            while time.time() < deadline:
                project = self._get_project(project_id)
                if project.brief["search_terms"] == ["new term a", "new term b"]:
                    break
                time.sleep(0.1)

        project = self._get_project(project_id)
        self.assertEqual(project.brief["search_terms"], ["new term a", "new term b"])
        self.assertEqual(project.brief["script"], "a human-edited script")  # script itself untouched here
        mock_revise.assert_called_once()
        mock_write.assert_not_called()  # never a full Creative Director rewrite, never re-researched


class TestScriptReviewEndpoints(_BaseGateTest):
    """API-layer coverage: status-code mapping and the PATCH .../script
    autosave's human_edits diff capture (same pattern as Final Review's
    .../metadata endpoint - feeds the learning loop's retrospective)."""

    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient

        from app.asgi import app

        self.client = TestClient(app)

    def test_approve_script_endpoint_status_codes(self):
        response = self.client.post("/api/v1/projects/999999/approve-script")
        self.assertEqual(response.status_code, 404)

        project_id = self._create_project(ProjectStatus.PRODUCING)
        response = self.client.post(f"/api/v1/projects/{project_id}/approve-script")
        self.assertEqual(response.status_code, 409)

    def test_reject_script_endpoint(self):
        project_id = self._create_project(
            ProjectStatus.AWAITING_SCRIPT_APPROVAL, topic="t", content_type_id="fun_facts",
            brief=_fake_brief().model_dump(),
        )
        response = self.client.post(f"/api/v1/projects/{project_id}/reject-script", json={"notes": "not working"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._get_project(project_id).status, ProjectStatus.IDEA_PENDING.value)

    def test_regenerate_script_endpoint_reports_cap_as_409(self):
        config.agents["max_script_regenerations"] = 0
        project_id = self._create_project(
            ProjectStatus.AWAITING_SCRIPT_APPROVAL, topic="t", niche="n", brief=_fake_brief().model_dump(),
        )
        response = self.client.post(f"/api/v1/projects/{project_id}/regenerate-script", json={"notes": "again"})
        self.assertEqual(response.status_code, 409)

    def test_patch_script_updates_title_and_script_and_records_human_edit_diff(self):
        project_id = self._create_project(
            ProjectStatus.AWAITING_SCRIPT_APPROVAL,
            topic="t", niche="n", brief=_fake_brief("AI draft script").model_dump(),
        )
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.revise_search_terms", return_value=["a"]
        ):
            response = self.client.patch(
                f"/api/v1/projects/{project_id}/script",
                json={"title": "Human Title", "script": "Human-edited script"},
            )
        self.assertEqual(response.status_code, 200)

        project = self._get_project(project_id)
        self.assertEqual(project.brief["script"], "Human-edited script")
        self.assertEqual(project.brief["metadata_draft"]["working_title"], "Human Title")
        edits = {e["field"]: e for e in project.human_edits}
        self.assertEqual(edits["script"], {"field": "script", "before": "AI draft script", "after": "Human-edited script"})
        self.assertEqual(edits["title"], {"field": "title", "before": "Title", "after": "Human Title"})

    def test_patch_script_requires_awaiting_script_approval(self):
        project_id = self._create_project(ProjectStatus.PRODUCING, brief=_fake_brief().model_dump())
        response = self.client.patch(f"/api/v1/projects/{project_id}/script", json={"script": "x"})
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
