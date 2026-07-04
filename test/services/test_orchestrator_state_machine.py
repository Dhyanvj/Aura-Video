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
from app.agents.schemas import CreativeBrief, MetadataDraft
from app.db import session_scope
from app.db.models import ProjectStatus, VideoProject
from app.models import const
from app.services import state as sm


def _fake_brief() -> CreativeBrief:
    return CreativeBrief(
        script="A short punchy script.",
        search_terms=["clip a", "clip b"],
        music_direction="upbeat",
        bgm_file=None,
        voice_recommendation="en-US-GuyNeural-Male",
        subtitle_style="bottom, bold",
        metadata_draft=MetadataDraft(working_title="Title", hook_variants=["hook"]),
    )


def _fake_render_success(task_id, params, stop_at="video"):
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=5)
    for progress in (10, 50, 100):
        sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=progress)
    # Point at a nonexistent file so QA's real ffprobe check deterministically
    # reports "revise" without needing network or a vision model call.
    sm.state.update_task(
        task_id, state=const.TASK_STATE_COMPLETE, progress=100, videos=["/tmp/does-not-exist.mp4"], subtitle_path=None
    )


class TestOrchestratorStateMachine(unittest.TestCase):
    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

    def tearDown(self):
        db_session.engine = self._original_engine
        os.remove(self._db_path)

    def _get_project(self, project_id: int) -> VideoProject:
        with session_scope() as session:
            return session.get(VideoProject, project_id)

    def test_manual_project_fails_cleanly_without_anthropic_key(self):
        with patch.object(agent_base, "is_configured", return_value=False):
            project_id = orchestrator.start_manual_project(topic="a topic", niche="a niche")
            self._wait_for_status(project_id, {ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.FAILED.value)
        self.assertIn("anthropic_api_key", project.failure_reason)

    def test_full_pipeline_reaches_awaiting_human_approval_on_qa_pass(self):
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch("app.agents.producer.task_service.start") as mock_start, patch(
            "app.agents.base.BaseAgent.call_json_with_content"
        ) as mock_vision, patch(
            "app.agents.publisher.Publisher.prepare", return_value={"title_options": ["a", "b", "c"], "thumbnail_candidates": []}
        ):
            mock_start.side_effect = self._fake_render_pass
            from app.agents.schemas import VisionReview, FrameFinding

            mock_vision.return_value = VisionReview(
                overall="pass", frame_findings=[FrameFinding(frame_index=0, matches_script=True, notes="ok")]
            )

            project_id = orchestrator.start_manual_project(topic="a topic", niche="a niche")
            self._wait_for_status(project_id, {ProjectStatus.AWAITING_HUMAN_APPROVAL.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.AWAITING_HUMAN_APPROVAL.value)
        self.assertIsNotNone(project.brief)
        self.assertIsNotNone(project.publish_package)
        self.assertEqual(len(project.qa_reports), 1)
        self.assertEqual(project.qa_reports[0]["overall"], "pass")

    def test_revision_loop_caps_at_max_revisions_and_escalates(self):
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch("app.agents.producer.task_service.start", side_effect=_fake_render_success):
            project_id = orchestrator.start_manual_project(topic="a topic", niche="a niche")
            self._wait_for_status(project_id, {ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.FAILED.value)
        # Missing video -> QA always reports "revise"; max_revisions=2 means
        # 1 initial attempt + 2 revisions = 3 QA reports before escalating.
        self.assertEqual(project.revision_count, 2)
        self.assertEqual(len(project.qa_reports), 3)
        self.assertIn("limit (2)", project.failure_reason)

    def test_resume_incomplete_projects_reruns_projects_stuck_mid_pipeline(self):
        # Simulate a crash: a project left in PRODUCING (as if the process
        # died mid-render) should be picked back up and driven to completion
        # on the next startup, without any special resume-specific code path.
        with session_scope() as session:
            project = VideoProject(status=ProjectStatus.PRODUCING.value, topic="interrupted topic", niche="a niche")
            session.add(project)
            session.commit()
            session.refresh(project)
            project_id = project.id

        # A project NOT in an in-flight status must be left untouched.
        with session_scope() as session:
            done_project = VideoProject(status=ProjectStatus.PUBLISHED.value, topic="done topic", niche="a niche")
            session.add(done_project)
            session.commit()
            session.refresh(done_project)
            done_project_id = done_project.id

        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch("app.agents.producer.task_service.start", side_effect=_fake_render_success):
            orchestrator.resume_incomplete_projects()
            self._wait_for_status(project_id, {ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.FAILED.value)
        self.assertIsNotNone(project.brief)  # Creative Director actually reran

        untouched = self._get_project(done_project_id)
        self.assertEqual(untouched.status, ProjectStatus.PUBLISHED.value)

    def _fake_render_pass(self, task_id, params, stop_at="video"):
        # Use a real tiny ffmpeg-generated video so QA's technical checks pass.
        video_path = os.path.join(tempfile.gettempdir(), f"{task_id}.mp4")
        import subprocess

        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=24:duration=20",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video_path,
            ],
            capture_output=True,
            timeout=60,
        )
        self.addCleanup(lambda: os.path.exists(video_path) and os.remove(video_path))
        sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=5)
        for progress in (10, 50, 100):
            sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=progress)
        sm.state.update_task(
            task_id, state=const.TASK_STATE_COMPLETE, progress=100, videos=[video_path], subtitle_path=None
        )

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


if __name__ == "__main__":
    unittest.main()
