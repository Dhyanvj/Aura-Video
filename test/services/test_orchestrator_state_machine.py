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
from app.agents.researcher import Researcher
from app.agents.schemas import (
    CreativeBrief,
    FactCheckResult,
    Finding,
    FrameFinding,
    MetadataDraft,
    QAReport,
    QuoteOrLesson,
    ResearchDossier,
    TrendIdea,
    TrendReport,
    VerifiedQuote,
    VisionReview,
)
from app.agents.trend_scout import TrendScout
from app.config import config
from app.db import session_scope
from app.db.models import AgentEvent, ProjectStatus, VideoProject
from app.models import const
from app.services import cancellation, state as sm
from test.services._test_helpers import IsolatedStorageDirMixin


class _AutomaticApprovalModeMixin:
    """
    None of these tests are exercising the script-approval gate itself
    (see test_script_approval_gate.py for that) - they predate it and
    assert on QA/revision/resume/research-wiring behavior that expects the
    pipeline to run straight through script generation into production, the
    same as before the gate existed. Forcing automatic mode here keeps them
    deterministic regardless of the ambient config.toml's own
    autopilot_level/approval_mode value.
    """

    def setUp(self):
        super().setUp()
        self._original_approval_mode = config.agents.get("approval_mode")
        config.agents["approval_mode"] = "automatic"

    def tearDown(self):
        if self._original_approval_mode is None:
            config.agents.pop("approval_mode", None)
        else:
            config.agents["approval_mode"] = self._original_approval_mode
        super().tearDown()


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


class TestOrchestratorStateMachine(_AutomaticApprovalModeMixin, IsolatedStorageDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()
        self._start_isolated_storage_dir()

    def tearDown(self):
        db_session.engine = self._original_engine
        # Not deleted: orchestrator pipelines run in daemon threads that
        # outlive _wait_for_status returning (a revision loop's final
        # _set_status call can race the test method's return). Deleting the
        # temp file here lets a straggling thread silently recreate an
        # empty, tableless file at the same path on its next write/read,
        # corrupting whichever test happens to run next. A few KB leaked
        # into the OS temp dir per test is a fine trade for not having
        # cross-test corruption.
        self._stop_isolated_storage_dir()
        super().tearDown()

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

    def test_hallucinated_voice_recommendation_falls_back_instead_of_crashing_render(self):
        # Regression test: a live run had the Creative Director return a
        # free-text voice description (e.g. "a deep calm narrator voice")
        # instead of a real TTS voice ID, which crashed the render almost
        # immediately. _video_params_from_brief must substitute a valid
        # fallback voice rather than pass the bad value through.
        with session_scope() as session:
            project = VideoProject(status=ProjectStatus.SCRIPTING.value, topic="t", niche="n")
            session.add(project)
            session.commit()
            session.refresh(project)
            project_id = project.id

        bad_brief = CreativeBrief(
            script="test",
            search_terms=["a"],
            music_direction="calm",
            bgm_file=None,
            voice_recommendation="Deep, calm male documentary voice (Morgan Freeman-style)",
            subtitle_style="bottom",
            metadata_draft=MetadataDraft(working_title="t", hook_variants=[]),
        )

        params = orchestrator._video_params_from_brief(project_id, "topic", bad_brief)

        from sqlmodel import select

        from app.services import voice as voice_service
        from app.db.models import AgentEvent

        self.assertIn(params.voice_name, set(voice_service.get_all_azure_voices()))

        with session_scope() as session:
            events = session.exec(select(AgentEvent).where(AgentEvent.project_id == project_id)).all()
        self.assertTrue(any("invalid voice" in e.message for e in events))

    def test_video_params_carry_the_content_types_music_palette(self):
        # Different content types must end up with different bgm_palette
        # values so get_bgm_file() (app/services/video.py) actually draws
        # from a different pool of tracks per content type, instead of every
        # video using the same random-over-everything BGM selection.
        with session_scope() as session:
            ai_news_project = VideoProject(
                status=ProjectStatus.SCRIPTING.value, topic="t", niche="n", content_type_id="ai_news"
            )
            motivational_project = VideoProject(
                status=ProjectStatus.SCRIPTING.value, topic="t", niche="n", content_type_id="motivational"
            )
            session.add(ai_news_project)
            session.add(motivational_project)
            session.commit()
            session.refresh(ai_news_project)
            session.refresh(motivational_project)
            ai_news_project_id = ai_news_project.id
            motivational_project_id = motivational_project.id

        brief = _fake_brief()
        ai_news_params = orchestrator._video_params_from_brief(ai_news_project_id, "topic", brief)
        motivational_params = orchestrator._video_params_from_brief(motivational_project_id, "topic", brief)

        self.assertEqual(ai_news_params.bgm_palette, "tech_energetic")
        self.assertEqual(motivational_params.bgm_palette, "cinematic_uplifting")
        self.assertNotEqual(ai_news_params.bgm_palette, motivational_params.bgm_palette)

    def test_write_brief_passes_content_types_voice_style_to_creative_director(self):
        # voice_style (app/db/models.py ContentTypeTemplate) previously sat
        # unused in the DB; it must now reach the Creative Director so the
        # recommended voice actually reflects the content type's intended
        # tone (e.g. AI News: confident/energetic vs. World News: sober).
        with session_scope() as session:
            project = VideoProject(status=ProjectStatus.SCRIPTING.value, topic="t", niche="n", content_type_id="ai_news")
            session.add(project)
            session.commit()
            session.refresh(project)
            project_id = project.id

        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ) as mock_write:
            orchestrator._write_brief(project_id, "topic", "niche", revision_notes=None)

        self.assertIn("confident, energetic", mock_write.call_args.kwargs["voice_style"])

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

    def test_reject_with_notes_preserves_prior_render_under_revisions(self):
        # docs/DECISIONS_V3.md §4: "Reject with notes returns it to editing,
        # never deletes - prior render goes to revisions/." Exercises this
        # through the real orchestrator entry point a human uses
        # (retry_with_revision), not just project_storage directly.
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch("app.agents.producer.task_service.start") as mock_start, patch(
            "app.agents.base.BaseAgent.call_json_with_content"
        ) as mock_vision, patch(
            "app.agents.publisher.Publisher.prepare",
            return_value={"title_options": ["a", "b", "c"], "thumbnail_candidates": []},
        ):
            mock_start.side_effect = self._fake_render_pass
            from app.agents.schemas import FrameFinding, VisionReview

            mock_vision.return_value = VisionReview(
                overall="pass", frame_findings=[FrameFinding(frame_index=0, matches_script=True, notes="ok")]
            )

            project_id = orchestrator.start_manual_project(topic="a topic", niche="a niche")
            self._wait_for_status(project_id, {ProjectStatus.AWAITING_HUMAN_APPROVAL.value, ProjectStatus.FAILED.value})

            project = self._get_project(project_id)
            self.assertEqual(project.status, ProjectStatus.AWAITING_HUMAN_APPROVAL.value)
            storage_path = project.storage_path
            self.assertIsNotNone(storage_path)
            first_video_path = project.video_path

            orchestrator.retry_with_revision(project_id, "tighten the hook")
            self._wait_for_status(project_id, {ProjectStatus.AWAITING_HUMAN_APPROVAL.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.AWAITING_HUMAN_APPROVAL.value)
        # A fresh task_id per render means a fresh video file - the prior one
        # must not simply vanish.
        self.assertNotEqual(project.video_path, first_video_path)

        from app.utils import utils

        abs_dir = os.path.join(utils.storage_dir(), storage_path)
        revisions_dir = os.path.join(abs_dir, "revisions")
        self.assertTrue(os.path.isdir(revisions_dir))
        archived_videos = [
            os.path.join(root, f)
            for root, _, files in os.walk(revisions_dir)
            for f in files
            if f == "final-video.mp4"
        ]
        self.assertEqual(len(archived_videos), 1)
        # The current (second) render's video is present at the top level, not archived.
        self.assertTrue(os.path.isfile(os.path.join(abs_dir, "final-video.mp4")))

    def test_repeated_qa_finding_short_circuits_to_needs_human_review_without_exhausting_revisions(self):
        # A missing rendered file means QA can't extract frames and fails the
        # same deterministic file_exists check every round (see
        # QualityReviewer.review's no-frames fallback) - the SAME finding
        # fingerprint recurring after a revision means a revision cannot
        # resolve it (incident fix §4), so this escalates to
        # NEEDS_HUMAN_REVIEW after just one wasted revision attempt, well
        # short of the max_revisions=2 budget.
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch(
            "app.agents.creative_director.CreativeDirector.revise_search_terms", return_value=["clip a", "clip b"]
        ), patch("app.agents.producer.task_service.start", side_effect=_fake_render_success):
            project_id = orchestrator.start_manual_project(topic="a topic", niche="a niche")
            self._wait_for_status(project_id, {ProjectStatus.NEEDS_HUMAN_REVIEW.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.NEEDS_HUMAN_REVIEW.value)
        self.assertEqual(project.revision_count, 1)
        self.assertEqual(len(project.qa_reports), 2)
        self.assertIn("recurred", project.escalation_reason)
        self.assertIsNone(project.failure_reason)
        # The script itself was never rewritten - only search terms changed.
        self.assertEqual(project.brief["script"], _fake_brief().script)

    def test_revision_loop_caps_at_max_revisions_and_escalates_to_needs_human_review(self):
        # Each round's QA finding has a distinct fingerprint (a different
        # problem flagged each time), so the repeated-fingerprint short-
        # circuit never fires - this exercises genuinely exhausting the
        # automatic revision budget. FAILED must never be reached from a QA
        # outcome (incident fix §2) - only NEEDS_HUMAN_REVIEW, with the
        # rendered video preserved and playable.
        qa_reports = [
            QAReport(
                overall="revise",
                technical_checks=[],
                frame_findings=[],
                revision_target="producer",
                revision_notes=f"issue #{i}",
                findings=[
                    Finding(
                        category="visual",
                        fingerprint=f"visual:issue-{i}",
                        severity="major",
                        message=f"issue #{i}",
                        revision_target="producer",
                    )
                ],
            )
            for i in range(3)
        ]
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch(
            "app.agents.creative_director.CreativeDirector.revise_search_terms", return_value=["clip a", "clip b"]
        ), patch("app.agents.producer.task_service.start", side_effect=_fake_render_success), patch(
            "app.agents.quality_reviewer.QualityReviewer.review", side_effect=qa_reports
        ):
            project_id = orchestrator.start_manual_project(topic="a topic", niche="a niche")
            self._wait_for_status(project_id, {ProjectStatus.NEEDS_HUMAN_REVIEW.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.NEEDS_HUMAN_REVIEW.value)
        self.assertEqual(project.revision_count, 2)
        self.assertEqual(len(project.qa_reports), 3)
        self.assertIn("limit (2)", project.escalation_reason)
        self.assertIsNone(project.failure_reason)
        self.assertIsNotNone(project.video_path)

    def test_materials_only_revision_keeps_script_and_succeeds_on_second_attempt(self):
        # revision_target="producer" must not throw away a working script -
        # it should only ask for new search terms and retry rendering.
        qa_reports = [
            QAReport(
                overall="revise",
                technical_checks=[],
                frame_findings=[],
                revision_target="producer",
                revision_notes="Frame 2 shows the wrong animal.",
            ),
            QAReport(overall="pass", technical_checks=[], frame_findings=[]),
        ]
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ) as mock_write, patch(
            "app.agents.creative_director.CreativeDirector.revise_search_terms", return_value=["new clip a"]
        ) as mock_revise_terms, patch(
            "app.agents.producer.task_service.start", side_effect=_fake_render_success
        ), patch(
            "app.agents.quality_reviewer.QualityReviewer.review", side_effect=qa_reports
        ), patch(
            "app.agents.publisher.Publisher.prepare",
            return_value={"title_options": ["a", "b", "c"], "thumbnail_candidates": []},
        ):
            project_id = orchestrator.start_manual_project(topic="a topic", niche="a niche")
            self._wait_for_status(
                project_id, {ProjectStatus.AWAITING_HUMAN_APPROVAL.value, ProjectStatus.FAILED.value}
            )

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.AWAITING_HUMAN_APPROVAL.value)
        self.assertEqual(project.revision_count, 1)
        self.assertEqual(len(project.qa_reports), 2)
        self.assertEqual(mock_write.call_count, 1)
        self.assertEqual(mock_revise_terms.call_count, 1)
        self.assertEqual(project.brief["script"], _fake_brief().script)
        self.assertEqual(project.brief["search_terms"], ["new clip a"])

    def test_creative_director_targeted_revision_rewrites_script(self):
        # revision_target="creative_director" (a script/narrative problem)
        # must still go through the full rewrite path.
        qa_reports = [
            QAReport(
                overall="revise",
                technical_checks=[],
                frame_findings=[],
                revision_target="creative_director",
                revision_notes="The hook is weak.",
            ),
            QAReport(overall="pass", technical_checks=[], frame_findings=[]),
        ]
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ) as mock_write, patch(
            "app.agents.creative_director.CreativeDirector.revise_search_terms"
        ) as mock_revise_terms, patch(
            "app.agents.producer.task_service.start", side_effect=_fake_render_success
        ), patch(
            "app.agents.quality_reviewer.QualityReviewer.review", side_effect=qa_reports
        ), patch(
            "app.agents.publisher.Publisher.prepare",
            return_value={"title_options": ["a", "b", "c"], "thumbnail_candidates": []},
        ):
            project_id = orchestrator.start_manual_project(topic="a topic", niche="a niche")
            self._wait_for_status(
                project_id, {ProjectStatus.AWAITING_HUMAN_APPROVAL.value, ProjectStatus.FAILED.value}
            )

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.AWAITING_HUMAN_APPROVAL.value)
        self.assertEqual(project.revision_count, 1)
        self.assertEqual(mock_write.call_count, 2)
        mock_revise_terms.assert_not_called()

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
            # The missing rendered file fails the same deterministic technical
            # check every round, so the repeated-fingerprint short-circuit
            # (incident fix §4) escalates to NEEDS_HUMAN_REVIEW well before
            # exhausting the revision budget - a QA outcome must never reach
            # FAILED (incident fix §2).
            self._wait_for_status(project_id, {ProjectStatus.NEEDS_HUMAN_REVIEW.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.NEEDS_HUMAN_REVIEW.value)
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


class TestOrchestratorResearchWiring(_AutomaticApprovalModeMixin, IsolatedStorageDirMixin, unittest.TestCase):
    """
    Part 3: content types with research_required must get a real,
    per-content-type-verified topic (from the Researcher, not a generic
    trend query), store it as research_evidence, and refuse to auto-pick an
    unverified topic in autopilot.
    """

    def setUp(self):
        super().setUp()
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()
        self._start_isolated_storage_dir()

    def tearDown(self):
        self._stop_isolated_storage_dir()
        db_session.engine = self._original_engine
        # Not deleted: orchestrator pipelines run in daemon threads that
        # outlive _wait_for_status returning (a revision loop's final
        # _set_status call can race the test method's return). Deleting the
        # temp file here lets a straggling thread silently recreate an
        # empty, tableless file at the same path on its next write/read,
        # corrupting whichever test happens to run next. A few KB leaked
        # into the OS temp dir per test is a fine trade for not having
        # cross-test corruption.
        super().tearDown()

    def _get_project(self, project_id: int) -> VideoProject:
        with session_scope() as session:
            return session.get(VideoProject, project_id)

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

    def test_manual_project_keeps_human_topic_and_stores_researcher_dossier(self):
        # _produce_and_review is stubbed out here since these tests only
        # care about research/scripting wiring, not the render pipeline -
        # letting a real Producer run would hit real Pexels/TTS calls.
        dossier = ResearchDossier(topic="octopuses have three hearts", sources=[])
        with patch.object(agent_base, "is_configured", return_value=True), patch.object(
            Researcher, "research", return_value=dossier
        ) as mock_research, patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ) as mock_write, patch.object(orchestrator, "_produce_and_review"):
            project_id = orchestrator.start_manual_project(
                topic="my chosen fact", niche="ocean", content_type_id="fun_facts"
            )
            self._wait_for_status(project_id, {ProjectStatus.SCRIPT_READY.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        # The human's topic is never overridden by what the Researcher returns.
        self.assertEqual(project.topic, "my chosen fact")
        self.assertEqual(project.research_evidence["topic"], "octopuses have three hearts")
        mock_research.assert_called_once()
        self.assertEqual(mock_write.call_args.kwargs["research_dossier"].topic, "octopuses have three hearts")

    def test_manual_project_reduced_verification_hard_fails_for_news_content_type(self):
        dossier = ResearchDossier(topic="unclear", reduced_verification=True)
        with patch.object(agent_base, "is_configured", return_value=True), patch.object(
            Researcher, "research", return_value=dossier
        ), patch("app.agents.creative_director.CreativeDirector.write") as mock_write:
            project_id = orchestrator.start_manual_project(topic="a story", niche="tech", content_type_id="ai_news")
            self._wait_for_status(project_id, {ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.FAILED.value)
        self.assertIn("could not verify", project.failure_reason)
        mock_write.assert_not_called()

    def test_manual_project_reduced_verification_does_not_hard_fail_non_news_content_type(self):
        dossier = ResearchDossier(topic="a life lesson", reduced_verification=True)
        with patch.object(agent_base, "is_configured", return_value=True), patch.object(
            Researcher, "research", return_value=dossier
        ), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ) as mock_write, patch.object(orchestrator, "_produce_and_review"):
            project_id = orchestrator.start_manual_project(
                topic="discipline", niche="self-improvement", content_type_id="motivational"
            )
            self._wait_for_status(project_id, {ProjectStatus.SCRIPT_READY.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.SCRIPT_READY.value)
        mock_write.assert_called_once()

    def test_auto_trend_pipeline_skips_trend_scout_for_research_required_content_type(self):
        dossier = ResearchDossier(topic="a verified fun fact")
        with patch.object(agent_base, "is_configured", return_value=True), patch.object(
            Researcher, "research", return_value=dossier
        ), patch.object(TrendScout, "scout") as mock_scout, patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch.object(orchestrator, "_produce_and_review"):
            project_id = orchestrator.start_auto_trend_project(
                niche="ocean", audience="general", content_type_id="fun_facts"
            )
            self._wait_for_status(project_id, {ProjectStatus.SCRIPT_READY.value, ProjectStatus.FAILED.value})

        mock_scout.assert_not_called()
        project = self._get_project(project_id)
        self.assertEqual(project.topic, "a verified fun fact")
        self.assertEqual(project.research_evidence["topic"], "a verified fun fact")

    def test_auto_trend_pipeline_fails_without_auto_picking_when_research_unverified(self):
        dossier = ResearchDossier(topic="unclear", reduced_verification=True)
        with patch.object(agent_base, "is_configured", return_value=True), patch.object(
            Researcher, "research", return_value=dossier
        ), patch("app.agents.creative_director.CreativeDirector.write") as mock_write:
            project_id = orchestrator.start_auto_trend_project(
                niche="tech", audience="general", content_type_id="ai_news"
            )
            self._wait_for_status(project_id, {ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.FAILED.value)
        self.assertIn("without evidence", project.failure_reason)
        mock_write.assert_not_called()

    def test_auto_trend_pipeline_evidence_gate_leaves_project_idle_when_no_idea_has_evidence(self):
        report = TrendReport(
            ideas=[
                TrendIdea(
                    title="no evidence idea",
                    why_trending="just a guess",
                    evidence=[],
                    target_emotion="curiosity",
                    estimated_competition="low",
                    suggested_format="fact",
                    opportunity_score=90,
                )
            ]
        )
        with patch.object(agent_base, "is_configured", return_value=True), patch.object(
            TrendScout, "scout", return_value=report
        ), patch("app.agents.creative_director.CreativeDirector.write") as mock_write:
            project_id = orchestrator.start_auto_trend_project(niche="tech", audience="general")
            self._wait_for_status(project_id, {ProjectStatus.IDEA_PENDING.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.IDEA_PENDING.value)
        mock_write.assert_not_called()

    def test_auto_trend_pipeline_ignores_evidence_free_ideas_even_if_highest_scoring(self):
        report = TrendReport(
            ideas=[
                TrendIdea(
                    title="unbacked but flashy",
                    why_trending="sounds fun",
                    evidence=[],
                    target_emotion="excitement",
                    estimated_competition="low",
                    suggested_format="fact",
                    opportunity_score=99,
                ),
                TrendIdea(
                    title="backed by a real signal",
                    why_trending="actually trending",
                    evidence=["https://example.com/proof"],
                    target_emotion="curiosity",
                    estimated_competition="medium",
                    suggested_format="fact",
                    opportunity_score=40,
                ),
            ]
        )
        with patch.object(agent_base, "is_configured", return_value=True), patch.object(
            TrendScout, "scout", return_value=report
        ), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch.object(orchestrator, "_produce_and_review"):
            project_id = orchestrator.start_auto_trend_project(niche="tech", audience="general")
            self._wait_for_status(project_id, {ProjectStatus.SCRIPT_READY.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.topic, "backed by a real signal")

    def test_trending_now_stores_trend_scout_evidence_as_research_evidence(self):
        report = TrendReport(
            ideas=[
                TrendIdea(
                    title="a viral challenge",
                    why_trending="spiking on YouTube this week",
                    evidence=["https://youtube.com/trending-signal"],
                    target_emotion="excitement",
                    estimated_competition="high",
                    suggested_format="story",
                    opportunity_score=85,
                )
            ]
        )
        with patch.object(agent_base, "is_configured", return_value=True), patch.object(
            TrendScout, "scout", return_value=report
        ), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch.object(orchestrator, "_produce_and_review"):
            project_id = orchestrator.start_auto_trend_project(
                niche="pop culture", audience="general", content_type_id="trending_now"
            )
            self._wait_for_status(project_id, {ProjectStatus.SCRIPT_READY.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.research_evidence["topic"], "a viral challenge")
        self.assertEqual(project.research_evidence["sources"][0]["url"], "https://youtube.com/trending-signal")

    def test_revision_retry_reuses_stored_dossier_instead_of_recalling_researcher(self):
        dossier = ResearchDossier(topic="a verified fact")
        qa_reports = [
            QAReport(
                overall="revise",
                technical_checks=[],
                frame_findings=[],
                revision_target="creative_director",
                revision_notes="tighten the hook",
            ),
            QAReport(overall="pass", technical_checks=[], frame_findings=[]),
        ]
        with patch.object(agent_base, "is_configured", return_value=True), patch.object(
            Researcher, "research", return_value=dossier
        ) as mock_research, patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ) as mock_write, patch(
            "app.agents.producer.task_service.start", side_effect=_fake_render_success
        ), patch(
            "app.agents.quality_reviewer.QualityReviewer.review", side_effect=qa_reports
        ), patch(
            "app.agents.publisher.Publisher.prepare",
            return_value={"title_options": ["a", "b", "c"], "thumbnail_candidates": []},
        ):
            project_id = orchestrator.start_manual_project(
                topic="a verified fact", niche="ocean", content_type_id="fun_facts"
            )
            self._wait_for_status(
                project_id, {ProjectStatus.AWAITING_HUMAN_APPROVAL.value, ProjectStatus.FAILED.value}
            )

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.AWAITING_HUMAN_APPROVAL.value)
        # One research call for the whole run (including the revision loop) -
        # the revision only needed the same already-verified facts, not a
        # fresh (and re-billed) research pass.
        mock_research.assert_called_once()
        self.assertEqual(mock_write.call_count, 2)
        self.assertEqual(mock_write.call_args_list[1].kwargs["research_dossier"].topic, "a verified fact")

    def test_recent_topics_dedupe_scoped_per_content_type(self):
        with session_scope() as session:
            session.add(VideoProject(topic="shared topic", content_type_id="fun_facts"))
            session.add(VideoProject(topic="shared topic", content_type_id="motivational"))
            session.commit()

        self.assertEqual(orchestrator._recent_topics(content_type_id="fun_facts"), ["shared topic"])
        self.assertEqual(orchestrator._recent_topics(content_type_id="motivational"), ["shared topic"])
        self.assertEqual(orchestrator._recent_topics(content_type_id="ai_news"), [])
        # No filter at all keeps the original global, cross-type behavior.
        self.assertEqual(len(orchestrator._recent_topics()), 2)

    def test_recent_topics_dedupe_scoped_per_series_overrides_content_type(self):
        series_id = orchestrator.create_series(content_type_id="motivational", title="Series A")
        other_series_id = orchestrator.create_series(content_type_id="motivational", title="Series B")
        with session_scope() as session:
            session.add(VideoProject(topic="series a topic", content_type_id="motivational", series_id=series_id))
            session.add(
                VideoProject(topic="series b topic", content_type_id="motivational", series_id=other_series_id)
            )
            session.commit()

        self.assertEqual(orchestrator._recent_topics(content_type_id="motivational", series_id=series_id), ["series a topic"])


class TestCancellationCheckpoints(_AutomaticApprovalModeMixin, IsolatedStorageDirMixin, unittest.TestCase):
    """
    Recycle Bin (docs/DECISIONS_V3.md): deleting an in-flight project must
    cancel it cleanly first. These exercise the cooperative-cancellation
    checkpoints wired into the real orchestrator/producer pipeline (not
    app/services/project_deletion.py, which is covered directly in
    test_project_deletion.py) - request_cancel() during a stage, then assert
    the pipeline stops at CANCELLED rather than continuing to spend on
    production, and never falls through to FAILED.
    """

    def setUp(self):
        super().setUp()
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()
        self._start_isolated_storage_dir()

    def tearDown(self):
        db_session.engine = self._original_engine
        self._stop_isolated_storage_dir()
        super().tearDown()

    def _get_project(self, project_id: int) -> VideoProject:
        with session_scope() as session:
            return session.get(VideoProject, project_id)

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

    def test_cancellation_requested_during_scripting_stops_before_any_production_spend(self):
        project_holder = {}

        def _write_and_request_cancel(*args, **kwargs):
            cancellation.request_cancel(project_holder["id"])
            return _fake_brief()

        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", side_effect=_write_and_request_cancel
        ), patch("app.agents.producer.task_service.start") as mock_start:
            project_id = orchestrator.start_manual_project(topic="a topic", niche="a niche")
            project_holder["id"] = project_id
            self._wait_for_status(project_id, {ProjectStatus.CANCELLED.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.CANCELLED.value)
        mock_start.assert_not_called()  # zero production spend past the cancellation point
        with session_scope() as session:
            from sqlmodel import select

            events = session.exec(select(AgentEvent).where(AgentEvent.project_id == project_id)).all()
        self.assertTrue(any("Cancelled by user request" in e.message for e in events))

    def test_cancellation_requested_during_render_stops_at_cancelled_not_failed(self):
        project_holder = {}

        def _render_and_request_cancel(task_id, params, stop_at="video"):
            cancellation.request_cancel(project_holder["id"])
            sm.state.update_task(task_id, state=const.TASK_STATE_COMPLETE, progress=100, videos=["/tmp/x.mp4"])

        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch("app.agents.producer.task_service.start", side_effect=_render_and_request_cancel):
            project_id = orchestrator.start_manual_project(topic="a topic", niche="a niche")
            project_holder["id"] = project_id
            self._wait_for_status(project_id, {ProjectStatus.CANCELLED.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.CANCELLED.value)
        self.assertEqual(project.qa_reports, None)  # never reached QA - cancelled before the render was trusted

    def test_retry_after_cancellation_clears_the_flag_and_reruns(self):
        with session_scope() as session:
            project = VideoProject(
                status=ProjectStatus.CANCELLED.value, topic="t", niche="n", cancel_requested=True
            )
            session.add(project)
            session.commit()
            session.refresh(project)
            project_id = project.id

        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch.object(orchestrator, "_produce_and_review"):
            orchestrator.retry_failed_project(project_id)
            self._wait_for_status(project_id, {ProjectStatus.SCRIPT_READY.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.SCRIPT_READY.value)
        self.assertFalse(project.cancel_requested)


def _tsiolkovsky_brief() -> CreativeBrief:
    return CreativeBrief(
        script=(
            "Ever feel like you're stuck exactly where you started? Konstantin Tsiolkovsky once said: "
            '"Earth is the cradle of humanity, but one cannot live in a cradle forever." He wasn\'t just '
            "talking about rockets - every comfort zone was meant to raise you, not cage you. Outgrow it "
            "today."
        ),
        search_terms=["rocket launch", "earth from space"],
        music_direction="calm, cinematic",
        bgm_file=None,
        voice_recommendation="en-US-GuyNeural-Male",
        subtitle_style="bottom, bold",
        metadata_draft=MetadataDraft(working_title="Outgrow Your Cradle", hook_variants=["hook"]),
        quote_or_lesson=QuoteOrLesson(
            is_quote=True,
            text="Earth is the cradle of humanity, but one cannot live in a cradle forever.",
            attribution="Konstantin Tsiolkovsky",
        ),
    )


def _tsiolkovsky_dossier() -> ResearchDossier:
    return ResearchDossier(
        topic="Konstantin Tsiolkovsky on outgrowing comfort zones",
        verified_quote=VerifiedQuote(
            text="Earth is the cradle of humanity, but one cannot live in a cradle forever.",
            attribution="Konstantin Tsiolkovsky",
            verification_status="verified",
        ),
    )


class TestIncidentReplayMotivationalQuoteEscalation(_AutomaticApprovalModeMixin, IsolatedStorageDirMixin, unittest.TestCase):
    """
    Reconstructs the real incident this whole redesign is fixing: a
    Motivational Quotes video with a correctly-attributed, Researcher-
    verified quote, rejected twice by the old QA design for a re-litigated
    "uncertain" attribution and flagged "unsupported interpretations" on the
    unpacking lines - both of which the new calibration must not do. Only
    the genuinely minor visual notes (a slightly dark final frame, subtitle
    contrast) should surface, as warnings, never as a block.
    """

    def setUp(self):
        super().setUp()
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()
        self._start_isolated_storage_dir()

    def tearDown(self):
        db_session.engine = self._original_engine
        self._stop_isolated_storage_dir()
        super().tearDown()

    def _get_project(self, project_id: int) -> VideoProject:
        with session_scope() as session:
            return session.get(VideoProject, project_id)

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

    def _fake_render_pass(self, task_id, params, stop_at="video"):
        import subprocess

        video_path = os.path.join(tempfile.gettempdir(), f"{task_id}.mp4")
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

    def test_incident_replay_ends_in_final_review_with_warnings_never_failed(self):
        incident_vision = VisionReview(
            overall="pass",
            frame_findings=[
                FrameFinding(frame_index=0, matches_script=True, notes="ok"),
                FrameFinding(
                    frame_index=7,
                    matches_script=True,
                    issues=["final frame a bit dark"],
                    notes="still clearly visible, just not ideal",
                    severity="minor",
                    justification="polish preference, not a real problem",
                ),
                FrameFinding(
                    frame_index=6,
                    matches_script=True,
                    issues=["subtitle contrast could be better"],
                    notes="text is still legible",
                    severity="minor",
                    justification="polish preference, not a real problem",
                ),
            ],
        )

        def _fake_llm_call(*args, **kwargs):
            response_model = kwargs.get("response_model")
            if response_model is None and len(args) >= 3:
                response_model = args[2]
            if response_model is VisionReview:
                return incident_vision
            if response_model is FactCheckResult:
                # The properly-calibrated, content-type-aware fact-checker
                # does not flag the quote's unpacking/interpretation lines -
                # that was exactly the old design's bug.
                return FactCheckResult(flags=[])
            raise AssertionError(f"unexpected response_model {response_model!r}")

        with patch.object(agent_base, "is_configured", return_value=True), patch.object(
            Researcher, "research", return_value=_tsiolkovsky_dossier()
        ), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_tsiolkovsky_brief()
        ), patch(
            "app.agents.producer.task_service.start", side_effect=self._fake_render_pass
        ), patch(
            "app.agents.base.BaseAgent.call_json_with_content", side_effect=_fake_llm_call
        ), patch(
            "app.agents.publisher.Publisher.prepare",
            return_value={"title_options": ["a", "b", "c"], "thumbnail_candidates": []},
        ):
            project_id = orchestrator.start_manual_project(
                topic="Tsiolkovsky quote", niche="motivation", content_type_id="motivational"
            )
            self._wait_for_status(
                project_id,
                {
                    ProjectStatus.AWAITING_HUMAN_APPROVAL.value,
                    ProjectStatus.NEEDS_HUMAN_REVIEW.value,
                    ProjectStatus.FAILED.value,
                },
            )

        project = self._get_project(project_id)
        self.assertNotEqual(project.status, ProjectStatus.FAILED.value)
        self.assertEqual(project.status, ProjectStatus.AWAITING_HUMAN_APPROVAL.value)
        self.assertIsNotNone(project.video_path)
        self.assertTrue(os.path.isfile(project.video_path))
        self.assertEqual(len(project.qa_reports), 1)
        self.assertEqual(project.qa_reports[0]["overall"], "pass_with_warnings")


class TestScriptGateQuoteVerification(_AutomaticApprovalModeMixin, IsolatedStorageDirMixin, unittest.TestCase):
    """Incident fix §3: an unverifiable quote must be caught at the script gate, before any render happens."""

    def setUp(self):
        super().setUp()
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()
        self._start_isolated_storage_dir()

    def tearDown(self):
        db_session.engine = self._original_engine
        self._stop_isolated_storage_dir()
        super().tearDown()

    def _get_project(self, project_id: int) -> VideoProject:
        with session_scope() as session:
            return session.get(VideoProject, project_id)

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

    def test_unverifiable_quote_is_caught_at_script_gate_before_any_render(self):
        # No verified_quote in the dossier at all - the Creative Director's
        # own attempt (both the original and the one free rewrite) claims a
        # quote the Researcher never confirmed.
        dossier = ResearchDossier(topic="a life lesson topic")
        bad_brief = CreativeBrief(
            script="Some script asserting an unverifiable quote.",
            search_terms=["a"],
            music_direction="calm",
            bgm_file=None,
            voice_recommendation="en-US-GuyNeural-Male",
            subtitle_style="bottom",
            metadata_draft=MetadataDraft(working_title="t", hook_variants=[]),
            quote_or_lesson=QuoteOrLesson(is_quote=True, text="Some unverifiable quote.", attribution="Nobody Famous"),
        )

        with patch.object(agent_base, "is_configured", return_value=True), patch.object(
            Researcher, "research", return_value=dossier
        ), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=bad_brief
        ) as mock_write, patch("app.agents.producer.task_service.start") as mock_start:
            project_id = orchestrator.start_manual_project(
                topic="a quote topic", niche="motivation", content_type_id="motivational"
            )
            self._wait_for_status(project_id, {ProjectStatus.AWAITING_SCRIPT_APPROVAL.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        # Automatic approval mode is forced by _AutomaticApprovalModeMixin,
        # yet the project still stops here - an unverified quote overrides
        # automatic mode's normal skip-through.
        self.assertEqual(project.status, ProjectStatus.AWAITING_SCRIPT_APPROVAL.value)
        self.assertIsNotNone(project.script_verification_warning)
        mock_start.assert_not_called()  # no render ever happened
        self.assertEqual(mock_write.call_count, 2)  # original attempt + one free rewrite

    def test_dossier_verified_quote_proceeds_without_a_warning(self):
        with patch.object(agent_base, "is_configured", return_value=True), patch.object(
            Researcher, "research", return_value=_tsiolkovsky_dossier()
        ), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_tsiolkovsky_brief()
        ) as mock_write, patch.object(orchestrator, "_produce_and_review"):
            project_id = orchestrator.start_manual_project(
                topic="Tsiolkovsky quote", niche="motivation", content_type_id="motivational"
            )
            self._wait_for_status(project_id, {ProjectStatus.SCRIPT_READY.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.SCRIPT_READY.value)
        self.assertIsNone(project.script_verification_warning)
        mock_write.assert_called_once()  # no rewrite needed - verified on the first attempt


class TestNeedsHumanReviewActions(IsolatedStorageDirMixin, unittest.TestCase):
    """Human actions available at NEEDS_HUMAN_REVIEW: approve despite findings, request changes, reject."""

    def setUp(self):
        super().setUp()
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()
        self._start_isolated_storage_dir()

    def tearDown(self):
        db_session.engine = self._original_engine
        self._stop_isolated_storage_dir()
        super().tearDown()

    def _get_project(self, project_id: int) -> VideoProject:
        with session_scope() as session:
            return session.get(VideoProject, project_id)

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

    def _create_needs_review_project(self, findings=None) -> int:
        findings = (
            findings
            if findings is not None
            else [
                {
                    "category": "visual",
                    "fingerprint": "visual:frame1",
                    "severity": "major",
                    "message": "mismatch",
                    "overridable": True,
                }
            ]
        )
        qa_report = {
            "overall": "revise",
            "technical_checks": [],
            "frame_findings": [],
            "content_policy_flags": [],
            "revision_target": "producer",
            "revision_notes": "n",
            "fact_check_flags": [],
            "script_repetition_flag": None,
            "findings": findings,
        }
        with session_scope() as session:
            project = VideoProject(
                status=ProjectStatus.NEEDS_HUMAN_REVIEW.value,
                topic="t",
                niche="n",
                video_path="/tmp/does-not-exist-final.mp4",
                brief=_fake_brief().model_dump(),
                qa_reports=[qa_report],
                revision_count=2,
                escalation_reason="revision limit (2) reached",
            )
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    def test_approve_despite_findings_records_override_and_advances_to_awaiting_human_approval(self):
        project_id = self._create_needs_review_project()
        with patch(
            "app.agents.publisher.Publisher.prepare",
            return_value={"title_options": ["a", "b", "c"], "thumbnail_candidates": []},
        ):
            orchestrator.approve_despite_findings(project_id, overridden_fingerprints=["visual:frame1"])

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.AWAITING_HUMAN_APPROVAL.value)
        self.assertEqual(len(project.overridden_findings), 1)
        self.assertEqual(project.overridden_findings[0]["fingerprints"], ["visual:frame1"])
        self.assertIsNone(project.escalation_reason)

    def test_approve_despite_findings_rejects_non_overridable_hard_technical_critical(self):
        project_id = self._create_needs_review_project(
            findings=[
                {
                    "category": "technical",
                    "fingerprint": "technical:audio_present",
                    "severity": "critical",
                    "message": "no audio",
                    "overridable": False,
                }
            ]
        )
        with self.assertRaises(PermissionError):
            orchestrator.approve_despite_findings(project_id, overridden_fingerprints=["technical:audio_present"])
        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.NEEDS_HUMAN_REVIEW.value)

    def test_approve_despite_findings_requires_explicit_confirmation_for_policy_findings(self):
        project_id = self._create_needs_review_project(
            findings=[
                {
                    "category": "content_policy",
                    "fingerprint": "content_policy:medical-claim",
                    "severity": "critical",
                    "message": "medical claim",
                    "overridable": True,
                }
            ]
        )
        with self.assertRaises(PermissionError):
            orchestrator.approve_despite_findings(project_id, overridden_fingerprints=["content_policy:medical-claim"])

        with patch(
            "app.agents.publisher.Publisher.prepare",
            return_value={"title_options": ["a", "b", "c"], "thumbnail_candidates": []},
        ):
            orchestrator.approve_despite_findings(
                project_id, overridden_fingerprints=["content_policy:medical-claim"], confirm_policy_risk=True
            )
        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.AWAITING_HUMAN_APPROVAL.value)

    def test_approve_despite_findings_requires_needs_human_review_status(self):
        with session_scope() as session:
            project = VideoProject(status=ProjectStatus.FAILED.value, topic="t", niche="n")
            session.add(project)
            session.commit()
            session.refresh(project)
            project_id = project.id
        with self.assertRaises(PermissionError):
            orchestrator.approve_despite_findings(project_id)

    def test_request_changes_from_review_resets_revision_budget_and_reenters_production(self):
        project_id = self._create_needs_review_project()
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch.object(orchestrator, "_produce_and_review"):
            orchestrator.request_changes_from_review(project_id, "please fix the visuals")
            self._wait_for_status(project_id, {ProjectStatus.SCRIPT_READY.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.SCRIPT_READY.value)
        self.assertEqual(project.revision_count, 0)
        self.assertIsNone(project.escalation_reason)

    def test_reject_from_review_is_terminal_and_preserves_the_video_reference(self):
        project_id = self._create_needs_review_project()
        orchestrator.reject_from_review(project_id, "not good enough")
        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.REJECTED.value)
        self.assertEqual(project.video_path, "/tmp/does-not-exist-final.mp4")


if __name__ == "__main__":
    unittest.main()
