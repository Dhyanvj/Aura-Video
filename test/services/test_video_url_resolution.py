import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlmodel import create_engine

import app.db.session as db_session
from app.controllers.v1.pipeline import _resolve_video_url
from app.db import session_scope
from app.db.models import ProjectStatus, VideoProject
from app.services import project_storage
from app.utils import utils
from test.services._test_helpers import IsolatedStorageDirMixin


class TestResolveVideoUrl(IsolatedStorageDirMixin, unittest.TestCase):
    """
    Regression coverage: after a Failed-project rescue
    (orchestrator.rescue_failed_project), video_path can point at a render
    inside the project's OWN storage folder instead of the task scratch
    dir. The frontend's legacy /tasks/{task_id}/{filename} route can't serve
    that (wrong directory, not just a different filename) - it 404s and the
    video appears to have vanished. _resolve_video_url must route each case
    to the URL that actually serves the file.
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

    def _create_project(self, **fields) -> VideoProject:
        with session_scope() as session:
            project = VideoProject(status=ProjectStatus.FAILED.value, **fields)
            session.add(project)
            session.commit()
            session.refresh(project)
            return project

    def test_no_video_path_returns_none(self):
        project = self._create_project(topic="t", video_path=None, task_id="task-1")
        self.assertIsNone(_resolve_video_url(project))

    def test_task_dir_video_uses_the_legacy_task_route(self):
        # The normal, non-rescued case: video_path always lives under
        # storage/tasks/{task_id}/ for the project's whole life.
        project = self._create_project(
            topic="t", task_id="task-1", video_path="/some/where/storage/tasks/task-1/final-1.mp4"
        )
        self.assertEqual(_resolve_video_url(project), "/tasks/task-1/final-1.mp4")

    def test_normal_project_with_a_storage_folder_still_uses_the_task_route(self):
        # Having a materialized storage folder does NOT by itself mean
        # video_path lives there - only a rescue repoints video_path.
        project = self._create_project(topic="t", task_id="task-1", video_path="/tmp/does-not-matter/final-1.mp4")
        project_storage.ensure_project_storage_path(project.id)
        self.assertEqual(_resolve_video_url(project), "/tasks/task-1/final-1.mp4")

    def test_rescued_current_render_uses_the_project_files_route(self):
        with session_scope() as session:
            project = session.get(VideoProject, self._create_project(topic="t", task_id="task-1").id)
            relative = project_storage.ensure_project_storage_path(project.id)
            abs_dir = os.path.join(utils.storage_dir(), relative)
            video_path = os.path.join(abs_dir, "final-video.mp4")
            with open(video_path, "wb") as fh:
                fh.write(b"fake mp4 bytes")
            project.video_path = video_path
            session.add(project)
            session.commit()
            session.refresh(project)

        self.assertEqual(_resolve_video_url(project), f"/api/v1/projects/{project.id}/files/final-video.mp4")

    def test_rescued_revision_render_uses_the_project_files_route_with_the_nested_path(self):
        with session_scope() as session:
            project = session.get(VideoProject, self._create_project(topic="t", task_id="task-1").id)
            relative = project_storage.ensure_project_storage_path(project.id)
            abs_dir = os.path.join(utils.storage_dir(), relative)
            revision_dir = os.path.join(abs_dir, "revisions", "20260101T000000")
            os.makedirs(revision_dir, exist_ok=True)
            video_path = os.path.join(revision_dir, "final-video.mp4")
            with open(video_path, "wb") as fh:
                fh.write(b"fake mp4 bytes")
            project.video_path = video_path
            session.add(project)
            session.commit()
            session.refresh(project)

        self.assertEqual(
            _resolve_video_url(project),
            f"/api/v1/projects/{project.id}/files/revisions/20260101T000000/final-video.mp4",
        )

    def test_video_path_outside_both_known_locations_falls_back_to_task_route_if_task_id_present(self):
        project = self._create_project(topic="t", task_id="task-1", video_path="/tmp/some-orphan-file.mp4")
        self.assertEqual(_resolve_video_url(project), "/tasks/task-1/some-orphan-file.mp4")

    def test_video_path_with_no_task_id_and_not_in_storage_folder_returns_none(self):
        project = self._create_project(topic="t", task_id=None, video_path="/tmp/some-orphan-file.mp4")
        self.assertIsNone(_resolve_video_url(project))


if __name__ == "__main__":
    unittest.main()
