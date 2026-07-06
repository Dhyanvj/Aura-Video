import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlmodel import create_engine, select

import app.db.session as db_session
from app.agents.schemas import PlaybookBullet, PlaybookDistillation, RetrospectiveLesson
from app.db import session_scope
from app.db.models import LessonLearned, Playbook, VideoProject
from app.services import playbook


class TestPlaybookVersioning(unittest.TestCase):
    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

    def tearDown(self):
        db_session.engine = self._original_engine
        # Not deleted: see docs/REVIEW_FINDINGS.md.

    def test_create_version_starts_at_one_and_is_active(self):
        pb = playbook.create_version("creative_director", "fun_facts", [{"text": "a", "enabled": True}])
        self.assertEqual(pb.version, 1)
        self.assertTrue(pb.is_active)

    def test_create_version_deactivates_the_previous_one(self):
        first = playbook.create_version("creative_director", "fun_facts", [{"text": "a", "enabled": True}])
        second = playbook.create_version("creative_director", "fun_facts", [{"text": "b", "enabled": True}])

        with session_scope() as session:
            refreshed_first = session.get(Playbook, first.id)
        self.assertFalse(refreshed_first.is_active)
        self.assertTrue(second.is_active)
        self.assertEqual(second.version, 2)

    def test_get_active_bullets_only_returns_enabled_ones_from_the_active_version(self):
        playbook.create_version(
            "creative_director",
            "fun_facts",
            [{"text": "keep this", "enabled": True}, {"text": "disabled one", "enabled": False}],
        )
        self.assertEqual(playbook.get_active_bullets("creative_director", "fun_facts"), ["keep this"])

    def test_get_active_bullets_empty_when_no_playbook_exists(self):
        self.assertEqual(playbook.get_active_bullets("creative_director", "fun_facts"), [])

    def test_scoping_is_per_content_type_not_global(self):
        playbook.create_version("creative_director", "fun_facts", [{"text": "fun facts rule", "enabled": True}])
        self.assertEqual(playbook.get_active_bullets("creative_director", "motivational"), [])
        self.assertEqual(playbook.get_active_bullets("creative_director", "fun_facts"), ["fun facts rule"])

    def test_rollback_reactivates_a_prior_version_without_deleting_history(self):
        first = playbook.create_version("creative_director", "fun_facts", [{"text": "v1", "enabled": True}])
        playbook.create_version("creative_director", "fun_facts", [{"text": "v2", "enabled": True}])

        restored = playbook.rollback_to(first.id)
        self.assertTrue(restored.is_active)
        self.assertEqual(playbook.get_active_bullets("creative_director", "fun_facts"), ["v1"])

        versions = playbook.list_versions("creative_director", "fun_facts")
        self.assertEqual(len(versions), 2)  # both versions still exist

    def test_update_bullet_creates_a_new_version_rather_than_mutating_in_place(self):
        original = playbook.create_version(
            "creative_director", "fun_facts", [{"text": "a", "enabled": True}, {"text": "b", "enabled": True}]
        )
        updated = playbook.update_bullet(original.id, 1, enabled=False)

        self.assertNotEqual(updated.id, original.id)
        self.assertEqual(updated.version, 2)
        self.assertEqual(playbook.get_active_bullets("creative_director", "fun_facts"), ["a"])

        with session_scope() as session:
            refreshed_original = session.get(Playbook, original.id)
        self.assertEqual(refreshed_original.bullets[1]["enabled"], True)  # untouched, per history-preserving design

    def test_update_bullet_invalid_index_raises(self):
        pb = playbook.create_version("creative_director", "fun_facts", [{"text": "a", "enabled": True}])
        with self.assertRaises(ValueError):
            playbook.update_bullet(pb.id, 5, enabled=False)

    def test_rollback_unknown_playbook_raises(self):
        with self.assertRaises(ValueError):
            playbook.rollback_to(999999)


class TestLessonRecordingAndDistillationTrigger(unittest.TestCase):
    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

    def tearDown(self):
        db_session.engine = self._original_engine

    def _create_project(self) -> int:
        with session_scope() as session:
            project = VideoProject(topic="t")
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    def test_record_lessons_persists_rows_scoped_by_agent_and_content_type(self):
        project_id = self._create_project()
        lessons = [
            RetrospectiveLesson(agent="creative_director", actionable_rule="rule one"),
            RetrospectiveLesson(agent="quality_reviewer", actionable_rule="rule two"),
        ]
        playbook.record_lessons(project_id, "fun_facts", lessons)

        with session_scope() as session:
            rows = session.exec(select(LessonLearned).where(LessonLearned.project_id == project_id)).all()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r.agent for r in rows}, {"creative_director", "quality_reviewer"})

    def test_distillation_due_every_ten_lessons(self):
        project_id = self._create_project()
        for i in range(9):
            playbook.record_lessons(
                project_id, "fun_facts", [RetrospectiveLesson(agent="creative_director", actionable_rule=f"r{i}")]
            )
        self.assertFalse(playbook.is_distillation_due("creative_director", "fun_facts"))

        playbook.record_lessons(
            project_id, "fun_facts", [RetrospectiveLesson(agent="creative_director", actionable_rule="r10")]
        )
        self.assertTrue(playbook.is_distillation_due("creative_director", "fun_facts"))

    def test_distillation_not_due_with_zero_lessons(self):
        self.assertFalse(playbook.is_distillation_due("creative_director", "fun_facts"))


class TestDistillPlaybook(unittest.TestCase):
    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

    def tearDown(self):
        db_session.engine = self._original_engine

    def _create_project(self) -> int:
        with session_scope() as session:
            project = VideoProject(topic="t")
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    def test_distill_playbook_returns_none_with_no_lessons(self):
        self.assertIsNone(playbook.distill_playbook("creative_director", "fun_facts"))

    def test_distill_playbook_creates_a_new_version_from_the_llm_call(self):
        project_id = self._create_project()
        playbook.record_lessons(
            project_id, "fun_facts", [RetrospectiveLesson(agent="creative_director", actionable_rule="be concrete")]
        )
        fake_result = PlaybookDistillation(bullets=[PlaybookBullet(text="Always name a concrete visual subject.")])
        with patch("app.agents.base.BaseAgent.call_json", return_value=fake_result):
            new_version = playbook.distill_playbook("creative_director", "fun_facts")

        self.assertIsNotNone(new_version)
        self.assertEqual(new_version.version, 1)
        self.assertEqual(
            playbook.get_active_bullets("creative_director", "fun_facts"),
            ["Always name a concrete visual subject."],
        )

    def test_distill_playbook_failure_is_non_fatal(self):
        project_id = self._create_project()
        playbook.record_lessons(
            project_id, "fun_facts", [RetrospectiveLesson(agent="creative_director", actionable_rule="x")]
        )
        with patch("app.agents.base.BaseAgent.call_json", side_effect=RuntimeError("boom")):
            result = playbook.distill_playbook("creative_director", "fun_facts")
        self.assertIsNone(result)


class TestRunRetrospectiveOrchestration(unittest.TestCase):
    """
    app/agents/orchestrator.py::_run_retrospective wires Retrospective's
    output into playbook.record_lessons + a distillation check - exercised
    directly (not through the full approve_and_publish flow, already covered
    in test_approval_gate.py) so a broken wiring shows up here specifically.
    """

    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

    def tearDown(self):
        db_session.engine = self._original_engine

    def _create_project(self, **fields) -> int:
        with session_scope() as session:
            project = VideoProject(topic="t", **fields)
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    def test_records_lessons_from_the_retrospective_call(self):
        from app.agents import orchestrator
        from app.agents.retrospective import Retrospective

        project_id = self._create_project(
            content_type_id="fun_facts",
            qa_reports=[{"overall": "pass"}],
            brief={"script": "a script"},
        )
        lessons = [RetrospectiveLesson(agent="creative_director", actionable_rule="be concrete")]
        with patch.object(Retrospective, "run", return_value=lessons):
            orchestrator._run_retrospective(project_id)

        with session_scope() as session:
            rows = session.exec(select(LessonLearned).where(LessonLearned.project_id == project_id)).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].actionable_rule, "be concrete")

    def test_triggers_distillation_when_the_tenth_lesson_lands(self):
        from app.agents import orchestrator
        from app.agents.retrospective import Retrospective

        # 9 lessons already on the books for this (agent, content_type) pair.
        seed_project = self._create_project(content_type_id="fun_facts")
        for i in range(9):
            playbook.record_lessons(
                seed_project, "fun_facts", [RetrospectiveLesson(agent="creative_director", actionable_rule=f"r{i}")]
            )

        project_id = self._create_project(content_type_id="fun_facts", qa_reports=[], brief={"script": "s"})
        tenth_lesson = [RetrospectiveLesson(agent="creative_director", actionable_rule="the tenth lesson")]
        fake_distillation = PlaybookDistillation(bullets=[PlaybookBullet(text="curated bullet")])
        with patch.object(Retrospective, "run", return_value=tenth_lesson), patch(
            "app.agents.base.BaseAgent.call_json", return_value=fake_distillation
        ):
            orchestrator._run_retrospective(project_id)

        self.assertEqual(playbook.get_active_bullets("creative_director", "fun_facts"), ["curated bullet"])

    def test_no_lessons_does_not_raise_or_distill(self):
        from app.agents import orchestrator
        from app.agents.retrospective import Retrospective

        project_id = self._create_project(content_type_id="fun_facts", qa_reports=[], brief={"script": "s"})
        with patch.object(Retrospective, "run", return_value=[]):
            orchestrator._run_retrospective(project_id)  # must not raise
        self.assertIsNone(playbook.get_active_playbook("creative_director", "fun_facts"))

    def test_retrospective_failure_is_logged_not_raised(self):
        from app.agents import orchestrator
        from app.agents.retrospective import Retrospective
        from app.db.models import AgentEvent

        project_id = self._create_project(content_type_id="fun_facts", qa_reports=[], brief={"script": "s"})
        with patch.object(Retrospective, "run", side_effect=RuntimeError("boom")):
            orchestrator._run_retrospective(project_id)  # must not raise

        with session_scope() as session:
            events = session.exec(select(AgentEvent).where(AgentEvent.project_id == project_id)).all()
        self.assertTrue(any("Retrospective failed" in e.message for e in events))


if __name__ == "__main__":
    unittest.main()
