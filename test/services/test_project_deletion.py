import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlmodel import create_engine, select

import app.db.session as db_session
from app.db import session_scope
from app.db.models import AgentEvent, ProjectStatus, Series, TopicEmbedding, UsedFact, VideoProject, utcnow
from app.services import project_deletion, project_storage
from app.utils import file_security, utils
from test.services._test_helpers import IsolatedStorageDirMixin


class _DBAndStorageMixin(IsolatedStorageDirMixin):
    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()
        self._start_isolated_storage_dir()

    def tearDown(self):
        db_session.engine = self._original_engine
        # Not deleted: see test_orchestrator_state_machine.py's tearDown for why.
        self._stop_isolated_storage_dir()

    def _create_project(self, **fields) -> int:
        with session_scope() as session:
            project = VideoProject(**fields)
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    def _get_project(self, project_id: int) -> VideoProject:
        with session_scope() as session:
            return session.get(VideoProject, project_id)


class TestSoftDeleteAndRestore(_DBAndStorageMixin, unittest.TestCase):
    def test_soft_delete_sets_status_and_captures_prior_status(self):
        project_id = self._create_project(status=ProjectStatus.FAILED.value, topic="bad run")
        project_deletion.delete_project(project_id)

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.DELETED.value)
        self.assertEqual(project.status_before_delete, ProjectStatus.FAILED.value)
        self.assertIsNotNone(project.deleted_at)

    def test_restore_returns_to_prior_status(self):
        project_id = self._create_project(status=ProjectStatus.FAILED.value, topic="bad run")
        project_deletion.delete_project(project_id)

        result = project_deletion.restore_project(project_id)

        self.assertEqual(result["status"], ProjectStatus.FAILED.value)
        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.FAILED.value)
        self.assertIsNone(project.deleted_at)
        self.assertIsNone(project.status_before_delete)

    def test_double_soft_delete_is_rejected(self):
        project_id = self._create_project(status=ProjectStatus.FAILED.value)
        project_deletion.delete_project(project_id)
        with self.assertRaises(project_deletion.ProjectAlreadyDeletedError):
            project_deletion.delete_project(project_id)

    def test_restore_of_non_deleted_project_is_rejected(self):
        project_id = self._create_project(status=ProjectStatus.FAILED.value)
        with self.assertRaises(project_deletion.ProjectNotDeletedError):
            project_deletion.restore_project(project_id)

    def test_deleted_project_excluded_from_retention_zero_bin(self):
        # retention_days=0 means "skip the bin" - delete_project should go
        # straight to a permanent purge instead of leaving it DELETED.
        from app.config import config

        project_id = self._create_project(status=ProjectStatus.FAILED.value, topic="skip the bin")
        with patch.dict(config.storage, {"recycle_bin_retention_days": 0}):
            result = project_deletion.delete_project(project_id)

        self.assertTrue(result["permanent"])
        self.assertIsNone(self._get_project(project_id))


class TestBulkDelete(_DBAndStorageMixin, unittest.TestCase):
    def test_bulk_delete_moves_all_to_bin_and_reports_errors_separately(self):
        ids = [self._create_project(status=ProjectStatus.FAILED.value) for _ in range(3)]
        result = project_deletion.bulk_delete(ids + [999999])

        self.assertEqual(len(result["deleted"]), 3)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["project_id"], 999999)
        for project_id in ids:
            self.assertEqual(self._get_project(project_id).status, ProjectStatus.DELETED.value)


class TestOriginalityFingerprintRule(_DBAndStorageMixin, unittest.TestCase):
    """
    docs/DECISIONS_V3.md §2 / product spec: permanently deleting an
    unpublished project frees its topic for reuse (fingerprints purged);
    deleting a published one keeps the fingerprint forever so the dedupe
    engine never lets the system recreate content that's already live.
    """

    def test_unpublished_project_purge_removes_fingerprints(self):
        project_id = self._create_project(status=ProjectStatus.FAILED.value, topic="a bad attempt", published_at=None)
        with session_scope() as session:
            session.add(TopicEmbedding(project_id=project_id, text="a bad attempt.", embedding=[0.1, 0.2]))
            session.add(UsedFact(content_type_id="fun_facts", fact_hash="abc123", project_id=project_id, fact_text="x"))
            session.commit()

        project_deletion.delete_project(project_id, permanent=True)

        with session_scope() as session:
            self.assertEqual(session.exec(select(TopicEmbedding).where(TopicEmbedding.project_id == project_id)).all(), [])
            self.assertEqual(session.exec(select(UsedFact).where(UsedFact.project_id == project_id)).all(), [])

    def test_published_project_purge_keeps_fingerprints(self):
        project_id = self._create_project(
            status=ProjectStatus.TRACKING.value, topic="a published hit", published_at=utcnow()
        )
        with session_scope() as session:
            session.add(TopicEmbedding(project_id=project_id, text="a published hit.", embedding=[0.1, 0.2]))
            session.add(UsedFact(content_type_id="fun_facts", fact_hash="def456", project_id=project_id, fact_text="y"))
            session.commit()

        result = project_deletion.delete_project(project_id, permanent=True)

        self.assertFalse(result["freed_topic_for_reuse"])
        with session_scope() as session:
            self.assertEqual(len(session.exec(select(TopicEmbedding).where(TopicEmbedding.project_id == project_id)).all()), 1)
            self.assertEqual(len(session.exec(select(UsedFact).where(UsedFact.project_id == project_id)).all()), 1)


class TestPurgeRemovesDbRowsAndFolder(_DBAndStorageMixin, unittest.TestCase):
    def test_purge_removes_agent_events_and_project_row(self):
        project_id = self._create_project(status=ProjectStatus.FAILED.value, topic="cleanup me")
        with session_scope() as session:
            session.add(AgentEvent(project_id=project_id, agent="orchestrator", type="output", message="hi"))
            session.commit()

        project_deletion.delete_project(project_id, permanent=True)

        self.assertIsNone(self._get_project(project_id))
        with session_scope() as session:
            self.assertEqual(session.exec(select(AgentEvent).where(AgentEvent.project_id == project_id)).all(), [])

    def test_purge_removes_project_folder_and_leaves_siblings_untouched(self):
        target_id = self._create_project(status=ProjectStatus.FAILED.value, topic="delete this one", content_type_id="fun_facts")
        sibling_id = self._create_project(status=ProjectStatus.FAILED.value, topic="keep this one", content_type_id="fun_facts")

        target_dir = project_storage.project_abs_dir(target_id) or os.path.join(
            utils.storage_dir(), project_storage.ensure_project_storage_path(target_id)
        )
        project_storage.ensure_project_storage_path(target_id)
        project_storage.ensure_project_storage_path(sibling_id)
        target_dir = project_storage.project_abs_dir(target_id)
        sibling_dir = project_storage.project_abs_dir(sibling_id)
        Path(target_dir, "final-video.mp4").write_bytes(b"fake")
        Path(sibling_dir, "final-video.mp4").write_bytes(b"fake")

        project_deletion.delete_project(target_id, permanent=True)

        self.assertFalse(os.path.exists(target_dir))
        self.assertTrue(os.path.isdir(sibling_dir))
        self.assertTrue(os.path.isfile(os.path.join(sibling_dir, "final-video.mp4")))


class TestDeletionSecurityControls(_DBAndStorageMixin, unittest.TestCase):
    """
    Extends TestSecurityControls-style coverage (test_video.py,
    test_project_storage.py::TestProjectFileRouteSecurity) to project
    deletion: filesystem removal only ever resolves a project's canonical
    folder from the DB and verifies it's strictly inside storage/projects/
    (or legacy storage/tasks/) before removal - never a client-supplied path.
    """

    def test_traversal_attempt_is_refused(self):
        with self.assertRaises(ValueError):
            file_security.resolve_directory_for_deletion(project_storage.projects_root(create=True), "../../etc")

    def test_symlink_escape_is_refused(self):
        projects_root = project_storage.projects_root(create=True)
        outside = tempfile.mkdtemp()
        try:
            link_path = os.path.join(projects_root, "escape-link")
            os.symlink(outside, link_path)
            with self.assertRaises(ValueError):
                file_security.resolve_directory_for_deletion(projects_root, "escape-link")
        finally:
            import shutil

            shutil.rmtree(outside, ignore_errors=True)

    def test_root_deletion_is_refused(self):
        projects_root = project_storage.projects_root(create=True)
        with self.assertRaises(ValueError):
            file_security.resolve_directory_for_deletion(projects_root, ".")

    def test_purge_refuses_a_storage_path_that_escapes_the_projects_root(self):
        # Simulates a corrupted/malicious DB row rather than a client-supplied
        # path (the safety rule this exercises is "never trust storage_path
        # blindly", not just "never trust client input").
        project_id = self._create_project(
            status=ProjectStatus.FAILED.value, topic="corrupted row", storage_path="../../etc/passwd"
        )
        result = project_deletion.purge_project(project_id, require_deleted=False)
        self.assertFalse(result["folder_removed"])

    def test_purge_removes_exactly_one_projects_tree_and_nothing_else(self):
        ids = [
            self._create_project(status=ProjectStatus.FAILED.value, topic=f"project {i}", content_type_id="fun_facts")
            for i in range(4)
        ]
        dirs = {}
        for project_id in ids:
            project_storage.ensure_project_storage_path(project_id)
            abs_dir = project_storage.project_abs_dir(project_id)
            Path(abs_dir, "final-video.mp4").write_bytes(b"fake")
            dirs[project_id] = abs_dir

        target = ids[2]
        project_deletion.delete_project(target, permanent=True)

        self.assertFalse(os.path.exists(dirs[target]))
        for project_id in ids:
            if project_id == target:
                continue
            self.assertTrue(os.path.isdir(dirs[project_id]), f"sibling {project_id} was affected by target's purge")
            self.assertTrue(os.path.isfile(os.path.join(dirs[project_id], "final-video.mp4")))


class TestSeriesRecomputeOnDeletion(_DBAndStorageMixin, unittest.TestCase):
    def test_deleting_middle_episode_recomputes_rolling_summary(self):
        with session_scope() as session:
            series = Series(content_type_id="motivational", title="A Series")
            session.add(series)
            session.commit()
            session.refresh(series)
            series_id = series.id

        ep1 = self._create_project(
            status=ProjectStatus.PUBLISHED.value, topic="episode one", series_id=series_id, episode_number=1
        )
        ep2 = self._create_project(
            status=ProjectStatus.PUBLISHED.value, topic="episode two", series_id=series_id, episode_number=2
        )
        ep3 = self._create_project(
            status=ProjectStatus.PUBLISHED.value, topic="episode three", series_id=series_id, episode_number=3
        )
        with session_scope() as session:
            series = session.get(Series, series_id)
            series.rolling_summary = "Episode 1: episode one\nEpisode 2: episode two\nEpisode 3: episode three"
            session.add(series)
            session.commit()

        result = project_deletion.delete_project(ep2)

        self.assertIsNotNone(result["warning"])  # ep2 is a middle episode of an active series
        with session_scope() as session:
            series = session.get(Series, series_id)
        self.assertNotIn("episode two", series.rolling_summary)
        self.assertIn("episode one", series.rolling_summary)
        self.assertIn("episode three", series.rolling_summary)

    def test_deleting_last_episode_gives_no_middle_episode_warning(self):
        with session_scope() as session:
            series = Series(content_type_id="motivational", title="A Series")
            session.add(series)
            session.commit()
            session.refresh(series)
            series_id = series.id

        self._create_project(status=ProjectStatus.PUBLISHED.value, topic="ep one", series_id=series_id, episode_number=1)
        ep2 = self._create_project(status=ProjectStatus.PUBLISHED.value, topic="ep two", series_id=series_id, episode_number=2)

        result = project_deletion.delete_project(ep2)
        self.assertIsNone(result["warning"])


class TestPurgeExpired(_DBAndStorageMixin, unittest.TestCase):
    def test_purge_expired_removes_only_projects_past_retention(self):
        from datetime import timedelta

        from app.config import config

        expired_id = self._create_project(status=ProjectStatus.DELETED.value, topic="old")
        fresh_id = self._create_project(status=ProjectStatus.DELETED.value, topic="new")
        with session_scope() as session:
            expired = session.get(VideoProject, expired_id)
            expired.deleted_at = utcnow() - timedelta(days=10)
            session.add(expired)
            fresh = session.get(VideoProject, fresh_id)
            fresh.deleted_at = utcnow() - timedelta(days=1)
            session.add(fresh)
            session.commit()

        with patch.dict(config.storage, {"recycle_bin_retention_days": 7}):
            purged = project_deletion.purge_expired()

        self.assertEqual(purged, 1)
        self.assertIsNone(self._get_project(expired_id))
        self.assertIsNotNone(self._get_project(fresh_id))


class TestCancelThenDeleteInFlight(_DBAndStorageMixin, unittest.TestCase):
    def test_deleting_an_in_flight_project_cancels_it_first(self):
        project_id = self._create_project(status=ProjectStatus.PRODUCING.value, topic="mid render", niche="n")

        # No real orchestrator thread is running here (this test targets the
        # deletion service in isolation) - simulate one by flipping the
        # status to CANCELLED shortly after the cancellation request, the way
        # a real pipeline checkpoint would.
        def _simulate_pipeline_noticing_cancellation():
            time.sleep(0.2)
            with session_scope() as session:
                project = session.get(VideoProject, project_id)
                project.status = ProjectStatus.CANCELLED.value
                session.add(project)
                session.commit()

        import threading

        thread = threading.Thread(target=_simulate_pipeline_noticing_cancellation, daemon=True)
        thread.start()

        project_deletion.delete_project(project_id)
        thread.join(timeout=5)

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.DELETED.value)
        self.assertEqual(project.status_before_delete, ProjectStatus.CANCELLED.value)

    def test_deleting_an_in_flight_project_that_never_stops_raises(self):
        project_id = self._create_project(status=ProjectStatus.PRODUCING.value, topic="stuck render", niche="n")
        with patch.object(project_deletion, "_CANCEL_WAIT_TIMEOUT_S", 0.3):
            with self.assertRaises(TimeoutError):
                project_deletion.delete_project(project_id)

        # Refused, not silently soft-deleted out from under the still-running stage.
        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.PRODUCING.value)
        self.assertTrue(project.cancel_requested)


if __name__ == "__main__":
    unittest.main()
