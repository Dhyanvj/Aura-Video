import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlmodel import create_engine

import app.db.session as db_session
from app.agents import orchestrator
from app.config import config
from app.db import session_scope
from app.db.models import ProjectStatus, VideoProject


class TestApprovalGateEnforcement(unittest.TestCase):
    """
    The hard rule: nothing is ever published without a human approving a
    project that is actually awaiting approval. approve_and_publish() must
    raise rather than publish in every other case.
    """

    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

    def tearDown(self):
        db_session.engine = self._original_engine
        os.remove(self._db_path)

    def _create_project(self, status: ProjectStatus, **fields) -> int:
        with session_scope() as session:
            project = VideoProject(status=status.value, **fields)
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    def test_raises_for_nonexistent_project(self):
        with self.assertRaises(ValueError):
            orchestrator.approve_and_publish(999999, ["tiktok"])

    def test_raises_when_project_is_not_awaiting_approval(self):
        for status in (
            ProjectStatus.IDEA_PENDING,
            ProjectStatus.SCRIPTING,
            ProjectStatus.PRODUCING,
            ProjectStatus.RENDERED,
            ProjectStatus.QA_REVIEW,
            ProjectStatus.QA_PASSED,
            ProjectStatus.APPROVED,
            ProjectStatus.PUBLISHING,
            ProjectStatus.PUBLISHED,
            ProjectStatus.FAILED,
        ):
            project_id = self._create_project(
                status, video_path="/tmp/some-video.mp4", publish_package={"title_options": ["a"]}
            )
            with self.assertRaises(PermissionError, msg=f"expected refusal for status={status}"):
                orchestrator.approve_and_publish(project_id, ["tiktok"])

    def test_raises_when_awaiting_approval_but_missing_video_or_package(self):
        project_id = self._create_project(ProjectStatus.AWAITING_HUMAN_APPROVAL, video_path=None, publish_package=None)
        with self.assertRaises(RuntimeError):
            orchestrator.approve_and_publish(project_id, ["tiktok"])

    def test_succeeds_and_publishes_when_actually_awaiting_approval_and_publishing_enabled(self):
        # Publishing is on hold by default (see test_publish_skipped_when_publishing_disabled
        # below) - this covers the path once [features].publishing_enabled is flipped on.
        project_id = self._create_project(
            ProjectStatus.AWAITING_HUMAN_APPROVAL,
            video_path="/tmp/some-video.mp4",
            publish_package={"title_options": ["Title A"], "platform_variants": []},
        )
        with patch.dict(config.features, {"publishing_enabled": True}), patch(
            "app.agents.publisher.Publisher.publish", return_value=[{"success": True}]
        ):
            orchestrator.approve_and_publish(project_id, ["tiktok"])

            import time

            deadline = time.time() + 10
            status = None
            while time.time() < deadline:
                with session_scope() as session:
                    status = session.get(VideoProject, project_id).status
                if status in (ProjectStatus.PUBLISHED.value, ProjectStatus.TRACKING.value):
                    break
                time.sleep(0.1)

        with session_scope() as session:
            project = session.get(VideoProject, project_id)
        self.assertIn(project.status, (ProjectStatus.PUBLISHED.value, ProjectStatus.TRACKING.value))
        self.assertIsNotNone(project.published_at)

    def test_publish_skipped_when_publishing_disabled(self):
        # The default state: publishing is paused for the v2 quality redesign.
        # Approving marks the project complete without ever reaching
        # Publisher.publish() or the Upload-Post API.
        project_id = self._create_project(
            ProjectStatus.AWAITING_HUMAN_APPROVAL,
            video_path="/tmp/some-video.mp4",
            publish_package={"title_options": ["Title A"], "platform_variants": []},
        )
        with patch.dict(config.features, {"publishing_enabled": False}), patch(
            "app.agents.publisher.Publisher.publish"
        ) as mock_publish:
            orchestrator.approve_and_publish(project_id, [])
            mock_publish.assert_not_called()

        with session_scope() as session:
            project = session.get(VideoProject, project_id)
        self.assertEqual(project.status, ProjectStatus.ARCHIVED.value)
        self.assertIsNone(project.published_at)


class TestApproveEndpointPlatformValidation(unittest.TestCase):
    """
    The /approve endpoint only requires platforms when publishing is actually
    enabled - with it paused, approving with no platforms is the normal path
    (see docs/DESIGN_V2.md §4's repurposed Final Review page).
    """

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

    def _create_project(self) -> int:
        with session_scope() as session:
            project = VideoProject(
                status=ProjectStatus.AWAITING_HUMAN_APPROVAL.value,
                video_path="/tmp/some-video.mp4",
                publish_package={"title_options": ["Title A"], "platform_variants": []},
            )
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    def test_empty_platforms_allowed_when_publishing_disabled(self):
        project_id = self._create_project()
        with patch.dict(config.features, {"publishing_enabled": False}):
            response = self.client.post(f"/api/v1/projects/{project_id}/approve", json={"platforms": []})
        self.assertEqual(response.status_code, 200)

    def test_empty_platforms_rejected_when_publishing_enabled(self):
        project_id = self._create_project()
        with patch.dict(config.features, {"publishing_enabled": True}):
            response = self.client.post(f"/api/v1/projects/{project_id}/approve", json={"platforms": []})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
