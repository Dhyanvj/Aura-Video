import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlmodel import create_engine, select

import app.db.session as db_session
from app.agents.schemas import KeyFact, OriginalityJudgment, ResearchDossier
from app.db import session_scope
from app.db.models import TopicEmbedding, UsedFact, VideoProject
from app.services import originality


class TestPureMath(unittest.TestCase):
    def test_cosine_similarity_identical_vectors_is_one(self):
        self.assertAlmostEqual(originality.cosine_similarity([1.0, 0.0], [1.0, 0.0]), 1.0)

    def test_cosine_similarity_orthogonal_vectors_is_zero(self):
        self.assertAlmostEqual(originality.cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_cosine_similarity_handles_zero_vector(self):
        self.assertEqual(originality.cosine_similarity([0.0, 0.0], [1.0, 0.0]), 0.0)

    def test_normalize_fact_strips_punctuation_and_case(self):
        self.assertEqual(originality.normalize_fact("Octopuses Have THREE Hearts!"), "octopuses have three hearts")

    def test_fact_hash_is_stable_for_equivalent_wording(self):
        self.assertEqual(
            originality.fact_hash("Octopuses have three hearts."),
            originality.fact_hash("octopuses have three hearts"),
        )

    def test_fact_hash_differs_for_different_facts(self):
        self.assertNotEqual(originality.fact_hash("fact one"), originality.fact_hash("fact two"))


class TestScriptRepetition(unittest.TestCase):
    def test_identical_scripts_have_full_overlap(self):
        script = "The mantis shrimp punches faster than a speeding bullet in the open ocean today."
        self.assertEqual(originality.script_similarity_ratio(script, script), 1.0)

    def test_unrelated_scripts_have_low_overlap(self):
        a = "The mantis shrimp punches faster than a speeding bullet in the open ocean."
        b = "Discipline means keeping a promise to yourself when nobody else is watching you."
        self.assertLess(originality.script_similarity_ratio(a, b), originality.SCRIPT_REPETITION_THRESHOLD)

    def test_most_similar_script_picks_highest_overlap(self):
        script = "The mantis shrimp punches faster than a speeding bullet in the open ocean today."
        prior = [
            "Discipline means keeping a promise to yourself when nobody else is watching you.",
            "The mantis shrimp punches faster than a speeding bullet in the open ocean every day.",
        ]
        ratio, matched = originality.most_similar_script(script, prior)
        self.assertEqual(matched, prior[1])
        self.assertGreater(ratio, 0.5)

    def test_short_scripts_below_ngram_size_do_not_crash(self):
        self.assertEqual(originality.script_similarity_ratio("hi", "hi"), 0.0)


class TestDbBackedChecks(unittest.TestCase):
    def setUp(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._original_engine = db_session.engine
        db_session.engine = create_engine(f"sqlite:///{self._db_path}", connect_args={"check_same_thread": False})
        db_session.init_db()

    def tearDown(self):
        db_session.engine = self._original_engine
        # Not deleted: see docs/REVIEW_FINDINGS.md - avoids a straggling
        # background thread from a different test silently recreating an
        # empty file at this path.

    def _create_project(self, **fields) -> int:
        with session_scope() as session:
            project = VideoProject(**fields)
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.id

    # --- fact fingerprinting ---

    def test_find_reused_facts_only_applies_to_fingerprinted_content_types(self):
        project_id = self._create_project(topic="t", content_type_id="fun_facts")
        originality.record_used_facts(project_id, "fun_facts", ["Octopuses have three hearts."])

        # ai_news isn't in the fingerprinted set, even with an identical fact.
        self.assertEqual(originality.find_reused_facts("ai_news", ["Octopuses have three hearts."]), [])
        self.assertEqual(
            originality.find_reused_facts("fun_facts", ["Octopuses have three hearts!"]),
            ["Octopuses have three hearts!"],
        )

    def test_find_reused_facts_excludes_the_evaluating_projects_own_facts(self):
        project_id = self._create_project(topic="t", content_type_id="fun_facts")
        originality.record_used_facts(project_id, "fun_facts", ["Octopuses have three hearts."])

        # Retrying the SAME project must not match against its own prior commit.
        self.assertEqual(
            originality.find_reused_facts(
                "fun_facts", ["Octopuses have three hearts."], exclude_project_id=project_id
            ),
            [],
        )
        # A different project, no exclusion, does match.
        self.assertEqual(
            originality.find_reused_facts("fun_facts", ["Octopuses have three hearts."]),
            ["Octopuses have three hearts."],
        )

    def test_record_used_facts_is_idempotent(self):
        project_id = self._create_project(topic="t", content_type_id="fun_facts")
        originality.record_used_facts(project_id, "fun_facts", ["fact one", "fact two"])
        originality.record_used_facts(project_id, "fun_facts", ["fact one", "fact two"])
        with session_scope() as session:
            rows = session.exec(select(UsedFact).where(UsedFact.project_id == project_id)).all()
        self.assertEqual(len(rows), 2)

    # --- semantic dedupe (embeddings mocked - no real model load in unit tests) ---

    def test_check_topic_originality_high_similarity_rejects(self):
        project_a = self._create_project(topic="topic a", content_type_id="fun_facts")
        with session_scope() as session:
            session.add(
                TopicEmbedding(project_id=project_a, content_type_id="fun_facts", text="topic a", embedding=[1.0, 0.0])
            )
            session.commit()

        with patch.object(originality, "embed", return_value=[1.0, 0.0]):
            result = originality.check_topic_originality("fun_facts", None, "topic a rephrased")
        self.assertTrue(result.rejected)
        self.assertEqual(result.matched_project_id, project_a)

    def test_check_topic_originality_low_similarity_passes(self):
        project_a = self._create_project(topic="topic a", content_type_id="fun_facts")
        with session_scope() as session:
            session.add(
                TopicEmbedding(project_id=project_a, content_type_id="fun_facts", text="topic a", embedding=[1.0, 0.0])
            )
            session.commit()

        with patch.object(originality, "embed", return_value=[0.0, 1.0]):
            result = originality.check_topic_originality("fun_facts", None, "a totally different topic")
        self.assertFalse(result.rejected)

    def test_check_topic_originality_borderline_calls_judge(self):
        project_a = self._create_project(topic="topic a", content_type_id="fun_facts")
        with session_scope() as session:
            session.add(
                TopicEmbedding(project_id=project_a, content_type_id="fun_facts", text="topic a", embedding=[1.0, 0.0])
            )
            session.commit()

        # A vector at ~0.85 cosine similarity to [1, 0] lands in the borderline band.
        borderline_vector = [0.85, (1 - 0.85 ** 2) ** 0.5]
        with patch.object(originality, "embed", return_value=borderline_vector), patch.object(
            originality, "_judge_borderline", return_value=OriginalityJudgment(same_idea=False, rationale="new angle")
        ) as mock_judge:
            result = originality.check_topic_originality("fun_facts", None, "a related but distinct topic")
        mock_judge.assert_called_once()
        self.assertFalse(result.rejected)

    def test_check_topic_originality_scoped_by_content_type_not_global(self):
        project_a = self._create_project(topic="topic a", content_type_id="fun_facts")
        with session_scope() as session:
            session.add(
                TopicEmbedding(project_id=project_a, content_type_id="fun_facts", text="topic a", embedding=[1.0, 0.0])
            )
            session.commit()

        # Same embedding, but checked against a DIFFERENT content type - must not match.
        with patch.object(originality, "embed", return_value=[1.0, 0.0]):
            result = originality.check_topic_originality("motivational", None, "topic a rephrased")
        self.assertFalse(result.rejected)

    def test_check_topic_originality_excludes_own_project_on_retry(self):
        project_id = self._create_project(topic="topic a", content_type_id="fun_facts")
        with session_scope() as session:
            session.add(
                TopicEmbedding(project_id=project_id, content_type_id="fun_facts", text="topic a", embedding=[1.0, 0.0])
            )
            session.commit()

        # Without exclusion, the project would match its own prior embedding.
        with patch.object(originality, "embed", return_value=[1.0, 0.0]):
            unexcluded = originality.check_topic_originality("fun_facts", None, "topic a")
            excluded = originality.check_topic_originality(
                "fun_facts", None, "topic a", exclude_project_id=project_id
            )
        self.assertTrue(unexcluded.rejected)
        self.assertFalse(excluded.rejected)

    def test_embed_returns_none_when_model_unavailable_and_check_passes_open(self):
        with patch.object(originality, "_get_model", return_value=None):
            self.assertIsNone(originality.embed("anything"))
            result = originality.check_topic_originality("fun_facts", None, "anything")
        self.assertFalse(result.rejected)

    def test_record_topic_embedding_is_idempotent(self):
        project_id = self._create_project(topic="topic a", content_type_id="fun_facts")
        with patch.object(originality, "embed", return_value=[1.0, 0.0]):
            originality.record_topic_embedding(project_id, "fun_facts", None, "topic a")
            originality.record_topic_embedding(project_id, "fun_facts", None, "topic a")
        with session_scope() as session:
            rows = session.exec(select(TopicEmbedding).where(TopicEmbedding.project_id == project_id)).all()
        self.assertEqual(len(rows), 1)

    # --- evaluate_topic / commit_topic (combined entry points) ---

    def test_evaluate_topic_fact_reuse_takes_priority_over_similarity(self):
        project_a = self._create_project(topic="topic a", content_type_id="fun_facts")
        originality.record_used_facts(project_a, "fun_facts", ["Octopuses have three hearts."])

        project_b = self._create_project(topic="topic b", content_type_id="fun_facts")
        dossier = ResearchDossier(
            topic="a different framing", key_facts=[KeyFact(statement="Octopuses have three hearts.")]
        )
        with patch.object(originality, "embed", return_value=[0.0, 1.0]):  # would otherwise pass on similarity alone
            result = originality.evaluate_topic(project_b, "fun_facts", None, "a different framing", dossier)
        self.assertTrue(result.rejected)
        self.assertIn("already used", result.reason)

    def test_commit_topic_records_embedding_and_facts(self):
        project_id = self._create_project(topic="topic a", content_type_id="fun_facts")
        dossier = ResearchDossier(topic="topic a", key_facts=[KeyFact(statement="a verified fact")])
        with patch.object(originality, "embed", return_value=[1.0, 0.0]):
            originality.commit_topic(project_id, "fun_facts", None, "topic a", dossier)

        with session_scope() as session:
            embeddings = session.exec(select(TopicEmbedding).where(TopicEmbedding.project_id == project_id)).all()
            facts = session.exec(select(UsedFact).where(UsedFact.project_id == project_id)).all()
        self.assertEqual(len(embeddings), 1)
        self.assertEqual(len(facts), 1)


if __name__ == "__main__":
    unittest.main()
