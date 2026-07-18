import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlmodel import create_engine, select

import app.db.session as db_session
from app.agents import orchestrator
from app.db import session_scope
from app.db.models import AgentEvent, ProjectStatus, VideoProject
from app.services import project_storage, rescue
from test.services._test_helpers import IsolatedStorageDirMixin


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _run_ffmpeg(args) -> None:
    subprocess.run(["ffmpeg", "-y", *args], capture_output=True, timeout=60)


def _make_video(path: str, *, duration: int = 20, silent: bool = False, width: int = 1080, height: int = 1920) -> None:
    audio_source = f"anullsrc=r=44100:cl=stereo:d={duration}" if silent else f"sine=frequency=440:duration={duration}"
    _run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate=24:duration={duration}",
            "-f", "lavfi", "-i", audio_source,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path,
        ]
    )


@unittest.skipUnless(_ffmpeg_available(), "ffmpeg/ffprobe not available on PATH")
class TestCheckRenderTechnicallyValid(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="rescue_test_")
        cls.good_video = os.path.join(cls._tmp_dir, "good.mp4")
        _make_video(cls.good_video, duration=20)
        cls.silent_video = os.path.join(cls._tmp_dir, "silent.mp4")
        _make_video(cls.silent_video, duration=20, silent=True)
        cls.bad_resolution_video = os.path.join(cls._tmp_dir, "bad-res.mp4")
        _make_video(cls.bad_resolution_video, duration=20, width=640, height=480)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_missing_file_is_invalid(self):
        ok, reason = rescue.check_render_technically_valid("/tmp/definitely-does-not-exist-rescue.mp4")
        self.assertFalse(ok)
        self.assertIn("file_exists", reason)

    def test_good_video_is_valid(self):
        ok, reason = rescue.check_render_technically_valid(self.good_video)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "")

    def test_silent_audio_is_invalid_with_reason(self):
        ok, reason = rescue.check_render_technically_valid(self.silent_video)
        self.assertFalse(ok)
        self.assertIn("silent", reason)

    def test_wrong_resolution_is_invalid(self):
        ok, reason = rescue.check_render_technically_valid(self.bad_resolution_video)
        self.assertFalse(ok)
        self.assertIn("resolution", reason)


class _RescueDbTestCase(IsolatedStorageDirMixin, unittest.TestCase):
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

    def _create_project(self, status: ProjectStatus, **fields) -> int:
        with session_scope() as session:
            project = VideoProject(status=status.value, **fields)
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    def _get_project(self, project_id: int) -> VideoProject:
        with session_scope() as session:
            return session.get(VideoProject, project_id)


@unittest.skipUnless(_ffmpeg_available(), "ffmpeg/ffprobe not available on PATH")
class TestEvaluateRescuability(_RescueDbTestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="rescue_test_")
        cls.good_video = os.path.join(cls._tmp_dir, "good.mp4")
        _make_video(cls.good_video, duration=20)
        cls.silent_video = os.path.join(cls._tmp_dir, "silent.mp4")
        _make_video(cls.silent_video, duration=20, silent=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_no_render_anywhere_is_ineligible(self):
        project_id = self._create_project(ProjectStatus.FAILED, topic="t", video_path=None)
        eligibility = rescue.evaluate_rescuability(self._get_project(project_id))
        self.assertFalse(eligibility.eligible)
        self.assertIn("no rendered video", eligibility.reason)

    def test_silent_render_is_ineligible_with_reason(self):
        project_id = self._create_project(ProjectStatus.FAILED, topic="t", video_path=self.silent_video)
        eligibility = rescue.evaluate_rescuability(self._get_project(project_id))
        self.assertFalse(eligibility.eligible)
        self.assertIn("silent", eligibility.reason)

    def test_valid_render_is_eligible(self):
        project_id = self._create_project(ProjectStatus.FAILED, topic="t", video_path=self.good_video)
        eligibility = rescue.evaluate_rescuability(self._get_project(project_id))
        self.assertTrue(eligibility.eligible)
        self.assertEqual(eligibility.best.video_path, self.good_video)


@unittest.skipUnless(_ffmpeg_available(), "ffmpeg/ffprobe not available on PATH")
class TestListRenderCandidatesOrdering(_RescueDbTestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="rescue_test_")
        cls.good_video = os.path.join(cls._tmp_dir, "good.mp4")
        _make_video(cls.good_video, duration=20)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_current_then_revisions_newest_first_deduped(self):
        project_id = self._create_project(ProjectStatus.FAILED, topic="t", content_type_id=None)
        relative = project_storage.ensure_project_storage_path(project_id)
        from app.utils import utils

        abs_dir = os.path.join(utils.storage_dir(), relative)
        shutil.copy2(self.good_video, os.path.join(abs_dir, "final-video.mp4"))

        revisions_dir = os.path.join(abs_dir, "revisions")
        older = os.path.join(revisions_dir, "20260101T000000")
        newer = os.path.join(revisions_dir, "20260201T000000")
        os.makedirs(older, exist_ok=True)
        os.makedirs(newer, exist_ok=True)
        shutil.copy2(self.good_video, os.path.join(older, "final-video.mp4"))
        shutil.copy2(self.good_video, os.path.join(newer, "final-video.mp4"))

        project = self._get_project(project_id)
        candidates = rescue.list_render_candidates(project)
        self.assertEqual([c.id for c in candidates], ["current", "revision:20260201T000000", "revision:20260101T000000"])

    def test_non_timestamp_revision_folders_are_ignored(self):
        project_id = self._create_project(ProjectStatus.FAILED, topic="t")
        relative = project_storage.ensure_project_storage_path(project_id)
        from app.utils import utils

        abs_dir = os.path.join(utils.storage_dir(), relative)
        junk_dir = os.path.join(abs_dir, "revisions", "not-a-timestamp")
        os.makedirs(junk_dir, exist_ok=True)
        shutil.copy2(self.good_video, os.path.join(junk_dir, "final-video.mp4"))

        candidates = rescue.list_render_candidates(self._get_project(project_id))
        self.assertEqual(candidates, [])


@unittest.skipUnless(_ffmpeg_available(), "ffmpeg/ffprobe not available on PATH")
class TestOrchestratorRescueFailedProject(_RescueDbTestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="rescue_test_")
        cls.good_video = os.path.join(cls._tmp_dir, "good.mp4")
        _make_video(cls.good_video, duration=20)
        cls.silent_video = os.path.join(cls._tmp_dir, "silent.mp4")
        _make_video(cls.silent_video, duration=20, silent=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_eligible_project_is_rescued_into_needs_human_review(self):
        qa_reports = [{"overall": "revise", "technical_checks": [], "frame_findings": []}]
        project_id = self._create_project(
            ProjectStatus.FAILED,
            topic="t",
            video_path=self.good_video,
            failure_reason="publish-prep crashed: thumbnail generation failed",
            qa_reports=qa_reports,
        )

        result = orchestrator.rescue_failed_project(project_id)

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.NEEDS_HUMAN_REVIEW.value)
        self.assertEqual(result["status"], ProjectStatus.NEEDS_HUMAN_REVIEW.value)
        self.assertEqual(project.video_path, self.good_video)
        self.assertIsNone(project.failure_reason)
        # History preserved, never erased.
        self.assertEqual(project.qa_reports, qa_reports)
        self.assertEqual(len(project.rescue_history), 1)
        entry = project.rescue_history[0]
        self.assertEqual(entry["from_status"], ProjectStatus.FAILED.value)
        self.assertEqual(entry["to_status"], ProjectStatus.NEEDS_HUMAN_REVIEW.value)
        self.assertIn("thumbnail generation failed", entry["failure_reason"])
        # Informational banner carries the original reason forward.
        self.assertIn("thumbnail generation failed", project.escalation_reason)
        self.assertIn("overridden by you", project.escalation_reason)
        # The stale-badge cache is cleared - this is no longer a Failed project.
        self.assertIsNone(project.rescue_eligible)

        with session_scope() as session:
            events = session.exec(select(AgentEvent).where(AgentEvent.project_id == project_id)).all()
        self.assertTrue(any("Rescued from Failed" in e.message for e in events))

    def test_missing_render_cannot_be_rescued(self):
        project_id = self._create_project(ProjectStatus.FAILED, topic="t", video_path=None)
        with self.assertRaises(PermissionError):
            orchestrator.rescue_failed_project(project_id)
        self.assertEqual(self._get_project(project_id).status, ProjectStatus.FAILED.value)

    def test_silent_render_cannot_be_rescued(self):
        project_id = self._create_project(ProjectStatus.FAILED, topic="t", video_path=self.silent_video)
        with self.assertRaises(PermissionError) as ctx:
            orchestrator.rescue_failed_project(project_id)
        self.assertIn("silent", str(ctx.exception))
        self.assertEqual(self._get_project(project_id).status, ProjectStatus.FAILED.value)

    def test_stale_eligible_cache_does_not_bypass_the_fresh_recheck(self):
        # Rescuability is cached for cheap badge display, but the override
        # action itself must never trust it - the file could have been
        # removed (or degraded) since the cache was written.
        project_id = self._create_project(
            ProjectStatus.FAILED,
            topic="t",
            video_path=None,
            rescue_eligible=True,
            rescue_candidate_path="/tmp/does-not-exist-anymore.mp4",
        )
        with self.assertRaises(PermissionError):
            orchestrator.rescue_failed_project(project_id)

    def test_project_not_in_failed_status_is_rejected(self):
        # Simulates the race guard: retry_failed_project moves status off
        # FAILED before its background thread does any work, so a project
        # already mid-retry is never FAILED at the moment rescue reads it.
        project_id = self._create_project(ProjectStatus.SCRIPTING, topic="t", video_path=self.good_video)
        with self.assertRaises(PermissionError) as ctx:
            orchestrator.rescue_failed_project(project_id)
        self.assertIn("not FAILED", str(ctx.exception))

    def test_unknown_project_raises_value_error(self):
        with self.assertRaises(ValueError):
            orchestrator.rescue_failed_project(999999)

    def test_candidate_id_selects_a_specific_older_render(self):
        project_id = self._create_project(ProjectStatus.FAILED, topic="t")
        relative = project_storage.ensure_project_storage_path(project_id)
        from app.utils import utils

        abs_dir = os.path.join(utils.storage_dir(), relative)
        shutil.copy2(self.good_video, os.path.join(abs_dir, "final-video.mp4"))
        older_dir = os.path.join(abs_dir, "revisions", "20260101T000000")
        os.makedirs(older_dir, exist_ok=True)
        shutil.copy2(self.good_video, os.path.join(older_dir, "final-video.mp4"))

        result = orchestrator.rescue_failed_project(project_id, candidate_id="revision:20260101T000000")
        self.assertEqual(result["video_path"], os.path.join(older_dir, "final-video.mp4"))

    def test_unknown_candidate_id_raises_value_error(self):
        project_id = self._create_project(ProjectStatus.FAILED, topic="t", video_path=self.good_video)
        with self.assertRaises(ValueError):
            orchestrator.rescue_failed_project(project_id, candidate_id="revision:99990101T000000")
        self.assertEqual(self._get_project(project_id).status, ProjectStatus.FAILED.value)

    def test_rescue_history_accumulates_across_multiple_rescues(self):
        project_id = self._create_project(ProjectStatus.FAILED, topic="t", video_path=self.good_video)
        orchestrator.rescue_failed_project(project_id)
        with session_scope() as session:
            project = session.get(VideoProject, project_id)
            project.status = ProjectStatus.FAILED.value
            session.add(project)
            session.commit()
        orchestrator.rescue_failed_project(project_id)
        self.assertEqual(len(self._get_project(project_id).rescue_history), 2)


@unittest.skipUnless(_ffmpeg_available(), "ffmpeg/ffprobe not available on PATH")
class TestBackfillScan(_RescueDbTestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="rescue_test_")
        cls.good_video = os.path.join(cls._tmp_dir, "good.mp4")
        _make_video(cls.good_video, duration=20)
        cls.silent_video = os.path.join(cls._tmp_dir, "silent.mp4")
        _make_video(cls.silent_video, duration=20, silent=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_scan_flags_only_the_rescuable_failed_projects_and_never_transitions_anything(self):
        rescuable_id = self._create_project(ProjectStatus.FAILED, topic="rescuable", video_path=self.good_video)
        missing_id = self._create_project(ProjectStatus.FAILED, topic="missing", video_path=None)
        silent_id = self._create_project(ProjectStatus.FAILED, topic="silent", video_path=self.silent_video)
        # Not FAILED - must be skipped by the scan entirely, cache untouched.
        other_id = self._create_project(
            ProjectStatus.AWAITING_HUMAN_APPROVAL, topic="fine", video_path=self.good_video
        )

        summary = rescue.backfill_scan()
        self.assertEqual(summary, {"scanned": 3, "eligible": 1})

        rescuable = self._get_project(rescuable_id)
        self.assertTrue(rescuable.rescue_eligible)
        self.assertIsNotNone(rescuable.rescue_checked_at)
        self.assertEqual(rescuable.rescue_candidate_label, "last recorded render")
        self.assertEqual(rescuable.status, ProjectStatus.FAILED.value)  # never auto-rescued

        missing = self._get_project(missing_id)
        self.assertFalse(missing.rescue_eligible)
        self.assertIn("no rendered video", missing.rescue_ineligible_reason)
        self.assertEqual(missing.status, ProjectStatus.FAILED.value)

        silent = self._get_project(silent_id)
        self.assertFalse(silent.rescue_eligible)
        self.assertIn("silent", silent.rescue_ineligible_reason)
        self.assertEqual(silent.status, ProjectStatus.FAILED.value)

        other = self._get_project(other_id)
        self.assertIsNone(other.rescue_eligible)  # untouched - not a Failed project
        self.assertEqual(other.status, ProjectStatus.AWAITING_HUMAN_APPROVAL.value)


@unittest.skipUnless(_ffmpeg_available(), "ffmpeg/ffprobe not available on PATH")
class TestRescueEndpoints(_RescueDbTestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="rescue_test_")
        cls.good_video = os.path.join(cls._tmp_dir, "good.mp4")
        _make_video(cls.good_video, duration=20)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient

        from app.asgi import app

        self.client = TestClient(app)

    def test_eligibility_endpoint_reports_candidates(self):
        project_id = self._create_project(ProjectStatus.FAILED, topic="t", video_path=self.good_video)
        response = self.client.get(f"/api/v1/projects/{project_id}/rescue-eligibility")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertTrue(data["eligible"])
        self.assertEqual(len(data["candidates"]), 1)

    def test_rescue_endpoint_rejects_an_ineligible_project_server_side(self):
        project_id = self._create_project(ProjectStatus.FAILED, topic="t", video_path=None)
        response = self.client.post(f"/api/v1/projects/{project_id}/rescue", json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._get_project(project_id).status, ProjectStatus.FAILED.value)

    def test_rescue_endpoint_succeeds_for_an_eligible_project(self):
        project_id = self._create_project(ProjectStatus.FAILED, topic="t", video_path=self.good_video)
        response = self.client.post(f"/api/v1/projects/{project_id}/rescue", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._get_project(project_id).status, ProjectStatus.NEEDS_HUMAN_REVIEW.value)

    def test_rescue_scan_endpoint_runs_the_backfill(self):
        self._create_project(ProjectStatus.FAILED, topic="t", video_path=self.good_video)
        response = self.client.post("/api/v1/maintenance/rescue-scan")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], {"scanned": 1, "eligible": 1})


if __name__ == "__main__":
    unittest.main()
