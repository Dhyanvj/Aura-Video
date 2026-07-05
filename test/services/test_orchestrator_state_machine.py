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
from app.agents.schemas import CreativeBrief, MetadataDraft, QAReport, ResearchDossier, TrendIdea, TrendReport
from app.agents.trend_scout import TrendScout
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
        # Not deleted: orchestrator pipelines run in daemon threads that
        # outlive _wait_for_status returning (a revision loop's final
        # _set_status call can race the test method's return). Deleting the
        # temp file here lets a straggling thread silently recreate an
        # empty, tableless file at the same path on its next write/read,
        # corrupting whichever test happens to run next. A few KB leaked
        # into the OS temp dir per test is a fine trade for not having
        # cross-test corruption.
        pass

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
        # A missing rendered file means QA can't extract frames, so it always
        # reports "revise"/revision_target="producer" (see
        # QualityReviewer.review's no-frames fallback) - this exercises the
        # materials-only revision path (revise_search_terms), not a full
        # Creative Director rewrite.
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch(
            "app.agents.creative_director.CreativeDirector.revise_search_terms", return_value=["clip a", "clip b"]
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
        # The script itself was never rewritten - only search terms changed.
        self.assertEqual(project.brief["script"], _fake_brief().script)

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


class TestOrchestratorResearchWiring(unittest.TestCase):
    """
    Part 3: content types with research_required must get a real,
    per-content-type-verified topic (from the Researcher, not a generic
    trend query), store it as research_evidence, and refuse to auto-pick an
    unverified topic in autopilot.
    """

    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

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
        pass

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


if __name__ == "__main__":
    unittest.main()
