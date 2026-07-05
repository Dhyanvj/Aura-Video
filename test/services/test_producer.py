import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlmodel import create_engine

import app.db.session as db_session
from app.agents.producer import Producer
from app.db import session_scope
from app.db.models import ProjectStatus, VideoProject
from app.models import const
from app.models.schema import VideoParams
from app.services import state as sm


class TestProducerFailureReporting(unittest.TestCase):
    """
    Root-cause regression: Producer.run() used to raise a generic
    "render pipeline failed for task X" RuntimeError no matter why the
    render actually failed, so a Failed project card never showed anything
    actionable (Part 1, Step 3: "a Failed project card must show a one-line
    human-readable reason"). It must surface a specific failure_reason when
    the render pipeline attached one to the task state.
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

    def _create_project(self) -> int:
        with session_scope() as session:
            project = VideoProject(status=ProjectStatus.PRODUCING.value)
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    def test_surfaces_specific_failure_reason_from_task_state(self):
        def fake_start(task_id, params, stop_at="video"):
            sm.state.update_task(
                task_id,
                state=const.TASK_STATE_FAILED,
                failure_reason=(
                    "TTS produced unusable audio: audio is effectively silent "
                    "(mean volume -80.0 dB, threshold -50 dB)"
                ),
            )

        project_id = self._create_project()
        producer = Producer(project_id)
        with patch("app.agents.producer.task_service.start", side_effect=fake_start):
            with self.assertRaises(RuntimeError) as ctx:
                producer.run(VideoParams(video_subject="x"))

        self.assertIn("TTS produced unusable audio", str(ctx.exception))
        self.assertIn("silent", str(ctx.exception))

    def test_falls_back_to_generic_message_without_a_specific_reason(self):
        def fake_start(task_id, params, stop_at="video"):
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)

        project_id = self._create_project()
        producer = Producer(project_id)
        with patch("app.agents.producer.task_service.start", side_effect=fake_start):
            with self.assertRaises(RuntimeError) as ctx:
                producer.run(VideoParams(video_subject="x"))

        self.assertIn("render pipeline failed for task", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
