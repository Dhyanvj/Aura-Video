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
from app.agents import base as agent_base
from app.agents import orchestrator
from app.agents.schemas import CreativeBrief, MetadataDraft, QAReport, TrendIdea, TrendReport
from app.agents.trend_scout import TrendScout
from app.db import session_scope
from app.db.models import ProjectStatus, TopicEmbedding, VideoProject
from app.services import originality


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


class TestOriginalityGate(unittest.TestCase):
    """
    Wires app/services/originality.py into the orchestrator's pipeline
    (docs/DECISIONS_V3.md §2): a rejected topic must never reach scripting,
    a passing topic must be committed exactly once, and a revision re-entry
    (the same already-accepted topic being reworked) must never be gated.
    """

    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

    def tearDown(self):
        db_session.engine = self._original_engine
        # Not deleted: see docs/REVIEW_FINDINGS.md.

    def _get_project(self, project_id: int) -> VideoProject:
        with session_scope() as session:
            return session.get(VideoProject, project_id)

    def _wait_for_status(self, project_id: int, terminal_statuses: set, timeout: float = 30.0):
        deadline = time.time() + timeout
        status = None
        while time.time() < deadline:
            status = self._get_project(project_id).status
            if status in terminal_statuses:
                return status
            time.sleep(0.1)
        self.fail(f"project {project_id} never reached {terminal_statuses}, stuck at {status}")

    def test_rejected_topic_fails_before_scripting_and_never_calls_creative_director(self):
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write"
        ) as mock_write, patch.object(
            originality, "evaluate_topic", return_value=originality.OriginalityCheck(verdict="reject", reason="near-duplicate")
        ):
            project_id = orchestrator.start_manual_project(topic="a reused topic", niche="a niche")
            self._wait_for_status(project_id, {ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.FAILED.value)
        self.assertIn("Originality check rejected", project.failure_reason)
        mock_write.assert_not_called()

    def test_passing_topic_commits_embedding_exactly_once(self):
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch.object(orchestrator, "_produce_and_review"), patch.object(
            originality, "embed", return_value=[1.0, 0.0]
        ):
            project_id = orchestrator.start_manual_project(
                topic="a fresh topic", niche="a niche"
            )
            self._wait_for_status(project_id, {ProjectStatus.SCRIPT_READY.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.SCRIPT_READY.value)
        with session_scope() as session:
            rows = session.exec(select(TopicEmbedding).where(TopicEmbedding.project_id == project_id)).all()
        self.assertEqual(len(rows), 1)

    def test_revision_reentry_skips_the_originality_gate(self):
        # A QA "revise" verdict re-enters _run_pipeline with revision_notes
        # set and the SAME topic - this must never be re-evaluated (it isn't
        # a new idea) and must never double-commit the embedding.
        qa_reports = [
            QAReport(
                overall="revise", technical_checks=[], frame_findings=[],
                revision_target="creative_director", revision_notes="tighten the hook",
            ),
            QAReport(overall="pass", technical_checks=[], frame_findings=[]),
        ]
        with patch.object(agent_base, "is_configured", return_value=True), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch(
            "app.agents.producer.task_service.start"
        ) as mock_start, patch(
            "app.agents.quality_reviewer.QualityReviewer.review", side_effect=qa_reports
        ), patch(
            "app.agents.publisher.Publisher.prepare",
            return_value={"title_options": ["a", "b", "c"], "thumbnail_candidates": []},
        ), patch.object(
            originality, "embed", return_value=[1.0, 0.0]
        ) as mock_embed:
            from app.services import state as sm
            from app.models import const

            def fake_render(task_id, params, stop_at="video"):
                sm.state.update_task(task_id, state=const.TASK_STATE_COMPLETE, progress=100, videos=["/tmp/does-not-exist.mp4"], subtitle_path=None)

            mock_start.side_effect = fake_render

            project_id = orchestrator.start_manual_project(
                topic="a fresh topic", niche="a niche"
            )
            self._wait_for_status(
                project_id, {ProjectStatus.AWAITING_HUMAN_APPROVAL.value, ProjectStatus.FAILED.value}
            )

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.AWAITING_HUMAN_APPROVAL.value)
        with session_scope() as session:
            rows = session.exec(select(TopicEmbedding).where(TopicEmbedding.project_id == project_id)).all()
        # Exactly one commit despite the revision loop re-entering _run_pipeline a second time.
        self.assertEqual(len(rows), 1)

    def test_trend_scout_falls_back_to_next_ranked_idea_when_top_idea_is_rejected(self):
        report = TrendReport(
            ideas=[
                TrendIdea(
                    title="a reused idea", why_trending="seen before", evidence=["https://example.com/a"],
                    target_emotion="curiosity", estimated_competition="low", suggested_format="fact",
                    opportunity_score=90,
                ),
                TrendIdea(
                    title="a fresh idea", why_trending="new twist", evidence=["https://example.com/b"],
                    target_emotion="curiosity", estimated_competition="low", suggested_format="fact",
                    opportunity_score=50,
                ),
            ]
        )

        def fake_check(content_type_id, series_id, topic, angle=""):
            if topic == "a reused idea":
                return originality.OriginalityCheck(verdict="reject", reason="near-duplicate")
            return originality.OriginalityCheck(verdict="pass")

        with patch.object(agent_base, "is_configured", return_value=True), patch.object(
            TrendScout, "scout", return_value=report
        ), patch(
            "app.agents.creative_director.CreativeDirector.write", return_value=_fake_brief()
        ), patch.object(orchestrator, "_produce_and_review"), patch.object(
            originality, "check_topic_originality", side_effect=fake_check
        ), patch.object(
            originality, "evaluate_topic", return_value=originality.OriginalityCheck(verdict="pass")
        ):
            project_id = orchestrator.start_auto_trend_project(niche="tech", audience="general")
            self._wait_for_status(project_id, {ProjectStatus.SCRIPT_READY.value, ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.topic, "a fresh idea")

    def test_trend_scout_fails_cleanly_when_every_eligible_idea_is_rejected(self):
        report = TrendReport(
            ideas=[
                TrendIdea(
                    title="reused idea one", why_trending="seen before", evidence=["https://example.com/a"],
                    target_emotion="curiosity", estimated_competition="low", suggested_format="fact",
                    opportunity_score=90,
                ),
            ]
        )
        with patch.object(agent_base, "is_configured", return_value=True), patch.object(
            TrendScout, "scout", return_value=report
        ), patch(
            "app.agents.creative_director.CreativeDirector.write"
        ) as mock_write, patch.object(
            originality, "check_topic_originality",
            return_value=originality.OriginalityCheck(verdict="reject", reason="near-duplicate"),
        ):
            project_id = orchestrator.start_auto_trend_project(niche="tech", audience="general")
            self._wait_for_status(project_id, {ProjectStatus.FAILED.value})

        project = self._get_project(project_id)
        self.assertEqual(project.status, ProjectStatus.FAILED.value)
        self.assertIn("collided with prior coverage", project.failure_reason)
        mock_write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
