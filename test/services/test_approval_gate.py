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
from test.services._test_helpers import IsolatedStorageDirMixin


class TestApprovalGateEnforcement(IsolatedStorageDirMixin, unittest.TestCase):
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
        self._start_isolated_storage_dir()

    def tearDown(self):
        db_session.engine = self._original_engine
        self._stop_isolated_storage_dir()
        # Not deleted: a still-running daemon thread from this test can
        # otherwise reconnect after deletion and silently recreate an
        # empty, tableless file at the same path, corrupting the next test.
        pass

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
        # The default state: publishing is paused. Approving stops at
        # APPROVED (docs/DECISIONS_V3.md §4) - assets stay put and the
        # project surfaces in an "Approved / Ready to publish" queue -
        # without ever reaching Publisher.publish() or the Upload-Post API.
        # It no longer auto-archives; only mark_as_published() does that now.
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
        self.assertEqual(project.status, ProjectStatus.APPROVED.value)
        self.assertIsNone(project.published_at)


class TestMarkAsPublished(IsolatedStorageDirMixin, unittest.TestCase):
    """
    docs/DECISIONS_V3.md §4: while publishing_enabled=false, "Publish" means
    a human posts the video manually and then records it here.
    """

    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()
        self._start_isolated_storage_dir()

    def tearDown(self):
        db_session.engine = self._original_engine
        self._stop_isolated_storage_dir()
        # Not deleted: see docs/REVIEW_FINDINGS.md.

    def _create_project(self, status: ProjectStatus, **fields) -> int:
        with session_scope() as session:
            project = VideoProject(status=status.value, **fields)
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    def test_mark_as_published_requires_approved_status(self):
        project_id = self._create_project(ProjectStatus.AWAITING_HUMAN_APPROVAL)
        with self.assertRaises(PermissionError):
            orchestrator.mark_as_published(project_id, [])

    def test_mark_as_published_records_urls_and_moves_to_tracking(self):
        project_id = self._create_project(ProjectStatus.APPROVED, video_path="/tmp/v.mp4")
        orchestrator.mark_as_published(
            project_id, [{"platform": "youtube", "url": "https://youtube.com/watch?v=abc123"}]
        )

        with session_scope() as session:
            project = session.get(VideoProject, project_id)
        # TRACKING (not left at PUBLISHED) so run_performance_checks - which
        # only looks at TRACKING projects - can pick up the pasted URL.
        self.assertEqual(project.status, ProjectStatus.TRACKING.value)
        self.assertIsNotNone(project.published_at)
        self.assertEqual(len(project.published_posts), 1)
        self.assertEqual(project.published_posts[0]["platform"], "youtube")
        self.assertEqual(project.published_posts[0]["source"], "manual")

    def test_mark_as_published_allows_no_urls(self):
        # A human might publish somewhere with no meaningful stats URL to paste.
        project_id = self._create_project(ProjectStatus.APPROVED, video_path="/tmp/v.mp4")
        orchestrator.mark_as_published(project_id, [])

        with session_scope() as session:
            project = session.get(VideoProject, project_id)
        self.assertEqual(project.status, ProjectStatus.TRACKING.value)
        self.assertEqual(project.published_posts, [])

    def test_mark_as_published_unknown_project_raises(self):
        with self.assertRaises(ValueError):
            orchestrator.mark_as_published(999999, [])


class TestApproveEndpointPlatformValidation(IsolatedStorageDirMixin, unittest.TestCase):
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
        self._start_isolated_storage_dir()

        from fastapi.testclient import TestClient

        from app.asgi import app

        self.client = TestClient(app)

    def tearDown(self):
        self._stop_isolated_storage_dir()
        db_session.engine = self._original_engine
        # Not deleted: a still-running daemon thread from this test can
        # otherwise reconnect after deletion and silently recreate an
        # empty, tableless file at the same path, corrupting the next test.
        pass

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
