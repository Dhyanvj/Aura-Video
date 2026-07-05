"""
Originality engine (docs/DECISIONS_V3.md §2). Two independent mechanisms:

1. Semantic dedupe: a proposed topic+angle is embedded with a local, free
   sentence-transformer (no API cost) and compared via brute-force cosine
   against every prior project of the same content type (or series). High
   similarity auto-rejects; a borderline band gets one cheap Haiku-tier
   Claude call to judge "same idea or genuinely new perspective."
2. Per-type fact/quote fingerprinting: for Fun Facts and Motivational, the
   specific verified fact/quote (from the Researcher's dossier) is hashed and
   may only ever be used once, regardless of topic-level similarity.

Also provides the deterministic n-gram script-repetition check used by
Quality Reviewer (variety beyond topics).

Embeddings degrade gracefully: if sentence-transformers isn't installed or
fails to load, semantic dedupe is skipped (logged once) rather than raising -
the exact-string recent-topics check in orchestrator.py still runs regardless.
"""

import hashlib
import re
from typing import List, Optional

from loguru import logger
from sqlmodel import select

from app.agents.base import BaseAgent
from app.agents.schemas import OriginalityJudgment, ResearchDossier
from app.db import session_scope
from app.db.models import TopicEmbedding, UsedFact
from app.utils import utils

HIGH_SIMILARITY_THRESHOLD = 0.92
BORDERLINE_LOW_THRESHOLD = 0.80

# Fun Facts / Motivational only (docs/DECISIONS_V3.md §2 per-type rules) -
# AI/World News and Trending Now are handled by the general similarity check
# plus their existing freshness/evidence gates, not fact fingerprinting.
_FACT_FINGERPRINT_CONTENT_TYPES = {"fun_facts", "motivational"}

_MODEL_NAME = "all-MiniLM-L6-v2"
_model = None
_model_load_failed = False


def _get_model():
    global _model, _model_load_failed
    if _model is not None or _model_load_failed:
        return _model
    try:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(_MODEL_NAME)
    except Exception as exc:  # noqa: BLE001 - embeddings are a best-effort enhancement, never a hard dependency
        logger.warning(f"semantic dedupe unavailable, sentence-transformers failed to load: {exc}")
        _model_load_failed = True
    return _model


def embed(text: str) -> Optional[List[float]]:
    if not text:
        return None
    model = _get_model()
    if model is None:
        return None
    return model.encode(text).tolist()


def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class OriginalityCheck:
    """Plain result container - never serialized to an LLM prompt or API response as-is."""

    def __init__(
        self,
        verdict: str,  # "pass" | "reject"
        reason: str = "",
        similarity: float = 0.0,
        matched_project_id: Optional[int] = None,
    ):
        self.verdict = verdict
        self.reason = reason
        self.similarity = similarity
        self.matched_project_id = matched_project_id

    @property
    def rejected(self) -> bool:
        return self.verdict == "reject"


def _combined_text(topic: str, angle: str = "") -> str:
    return ". ".join(part.strip() for part in (topic, angle) if part and part.strip())


def check_topic_originality(
    content_type_id: Optional[str],
    series_id: Optional[int],
    topic: str,
    angle: str = "",
    judge_project_id: Optional[int] = None,
    exclude_project_id: Optional[int] = None,
) -> OriginalityCheck:
    """
    General semantic dedupe against every prior project of the same content
    type (or, if series_id is given, scoped to just that series - mirrors the
    scoping orchestrator._recent_topics already uses for the exact-string
    check). exclude_project_id must be set to the project being evaluated
    when it might already have committed its own embedding (e.g. retrying a
    project that failed AFTER passing this check on an earlier attempt) -
    otherwise a retry would compare the topic against itself and always
    "match" with similarity 1.0.
    """
    text = _combined_text(topic, angle)
    embedding = embed(text)
    if embedding is None:
        return OriginalityCheck(verdict="pass", reason="semantic dedupe unavailable, skipped")

    with session_scope() as session:
        query = select(TopicEmbedding)
        if series_id is not None:
            query = query.where(TopicEmbedding.series_id == series_id)
        elif content_type_id is not None:
            query = query.where(TopicEmbedding.content_type_id == content_type_id)
        if exclude_project_id is not None:
            query = query.where(TopicEmbedding.project_id != exclude_project_id)
        prior_snapshot = [(row.project_id, row.text, row.embedding) for row in session.exec(query).all()]

    best_similarity, best_match, best_text = 0.0, None, ""
    for project_id, prior_text, prior_embedding in prior_snapshot:
        similarity = cosine_similarity(embedding, prior_embedding)
        if similarity > best_similarity:
            best_similarity, best_match, best_text = similarity, project_id, prior_text

    if best_similarity >= HIGH_SIMILARITY_THRESHOLD:
        return OriginalityCheck(
            verdict="reject",
            reason=f"near-duplicate of project {best_match} ({best_similarity:.2f} cosine similarity)",
            similarity=best_similarity,
            matched_project_id=best_match,
        )

    if best_similarity >= BORDERLINE_LOW_THRESHOLD:
        judgment = _judge_borderline(text, best_text, judge_project_id)
        if judgment.same_idea:
            return OriginalityCheck(
                verdict="reject",
                reason=f"judged same idea as project {best_match}: {judgment.rationale}",
                similarity=best_similarity,
                matched_project_id=best_match,
            )
        return OriginalityCheck(
            verdict="pass",
            reason=f"borderline similarity but judged a genuinely new angle: {judgment.rationale}",
            similarity=best_similarity,
            matched_project_id=best_match,
        )

    return OriginalityCheck(verdict="pass", similarity=best_similarity, matched_project_id=best_match)


_JUDGE_SYSTEM_PROMPT = """You judge whether two short-form video concepts are the same idea reworded, or a
genuinely new angle. Compare the two summaries given. Respond with same_idea=true only if a
viewer who saw both videos would feel like they'd watched the same thing twice; same_idea=false
if there's a genuinely new fact, story development, or perspective."""


class _OriginalityJudge(BaseAgent):
    agent_name = "originality_judge"

    def __init__(self, project_id: Optional[int] = None):
        super().__init__(project_id)
        # Always cheap regardless of config.agents.model - this is a
        # mechanical same/different comparison, not a creative decision.
        self.model = "claude-haiku-4-5"


def _judge_borderline(candidate_text: str, prior_text: str, project_id: Optional[int]) -> OriginalityJudgment:
    from app.agents import base as agent_base

    if not agent_base.is_configured():
        # No key configured - conservatively treat borderline similarity as
        # the same idea rather than silently letting a likely-duplicate
        # through with no judge available to say otherwise.
        return OriginalityJudgment(same_idea=True, rationale="Anthropic API not configured; no judge available")

    judge = _OriginalityJudge(project_id)
    payload = {"candidate": candidate_text, "prior": prior_text}
    return judge.call_json(system=_JUDGE_SYSTEM_PROMPT, user=utils.to_json(payload), response_model=OriginalityJudgment)


def record_topic_embedding(
    project_id: int, content_type_id: Optional[str], series_id: Optional[int], topic: str, angle: str = ""
) -> None:
    with session_scope() as session:
        already_recorded = session.exec(select(TopicEmbedding.id).where(TopicEmbedding.project_id == project_id)).first()
        if already_recorded:
            return  # idempotent: a retried project must not double-record (or re-match against) itself

    text = _combined_text(topic, angle)
    embedding = embed(text)
    if embedding is None:
        return
    with session_scope() as session:
        session.add(
            TopicEmbedding(
                project_id=project_id, content_type_id=content_type_id, series_id=series_id, text=text, embedding=embedding
            )
        )
        session.commit()


def normalize_fact(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9 ]", "", (text or "").lower())
    return re.sub(r"\s+", " ", normalized).strip()


def fact_hash(text: str) -> str:
    return hashlib.sha256(normalize_fact(text).encode("utf-8")).hexdigest()


def find_reused_facts(
    content_type_id: Optional[str], fact_statements: List[str], exclude_project_id: Optional[int] = None
) -> List[str]:
    """
    Returns the subset of fact_statements whose fingerprint already exists for
    this content type. exclude_project_id (see check_topic_originality's
    docstring for why) prevents a retried project from matching its own
    previously-recorded facts.
    """
    if content_type_id not in _FACT_FINGERPRINT_CONTENT_TYPES:
        return []
    hashes = {fact_hash(text): text for text in fact_statements if normalize_fact(text)}
    if not hashes:
        return []
    with session_scope() as session:
        query = (
            select(UsedFact.fact_hash)
            .where(UsedFact.content_type_id == content_type_id)
            .where(UsedFact.fact_hash.in_(list(hashes.keys())))
        )
        if exclude_project_id is not None:
            query = query.where(UsedFact.project_id != exclude_project_id)
        existing = session.exec(query).all()
    return [hashes[h] for h in set(existing)]


def record_used_facts(project_id: int, content_type_id: Optional[str], fact_statements: List[str]) -> None:
    if content_type_id not in _FACT_FINGERPRINT_CONTENT_TYPES:
        return
    with session_scope() as session:
        already_recorded = session.exec(select(UsedFact.id).where(UsedFact.project_id == project_id)).first()
        if already_recorded:
            return  # idempotent: a retried project must not double-record its own facts
        for text in fact_statements:
            if not normalize_fact(text):
                continue
            session.add(
                UsedFact(content_type_id=content_type_id, fact_hash=fact_hash(text), project_id=project_id, fact_text=text)
            )
        session.commit()


def evaluate_topic(
    project_id: int,
    content_type_id: Optional[str],
    series_id: Optional[int],
    topic: str,
    dossier: Optional[ResearchDossier] = None,
) -> OriginalityCheck:
    """
    Single entry point the orchestrator calls before committing to a topic.
    Checks fact/quote fingerprints first (a hard, unambiguous rule for the
    content types it applies to) before falling back to general semantic
    similarity - a literal fact reuse doesn't need a similarity score.
    """
    angle = (dossier.suggested_angle or dossier.why_now) if dossier else ""

    if dossier and dossier.key_facts:
        statements = [fact.statement for fact in dossier.key_facts]
        reused = find_reused_facts(content_type_id, statements, exclude_project_id=project_id)
        if reused:
            return OriginalityCheck(
                verdict="reject",
                reason=f"fact/quote already used in a prior project: {reused[0]!r}",
            )

    return check_topic_originality(
        content_type_id, series_id, topic, angle, judge_project_id=project_id, exclude_project_id=project_id
    )


def commit_topic(
    project_id: int,
    content_type_id: Optional[str],
    series_id: Optional[int],
    topic: str,
    dossier: Optional[ResearchDossier] = None,
) -> None:
    """Called once a topic passes evaluate_topic and scripting is about to begin - records the embedding and
    fact fingerprints so future checks compare against this project too."""
    angle = (dossier.suggested_angle or dossier.why_now) if dossier else ""
    record_topic_embedding(project_id, content_type_id, series_id, topic, angle)
    if dossier and dossier.key_facts:
        record_used_facts(project_id, content_type_id, [fact.statement for fact in dossier.key_facts])


_NGRAM_SIZE = 4
SCRIPT_REPETITION_THRESHOLD = 0.5


def _ngrams(text: str, n: int = _NGRAM_SIZE) -> set:
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    if len(words) < n:
        return set()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def script_similarity_ratio(script: str, other_script: str) -> float:
    a, b = _ngrams(script), _ngrams(other_script)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a)


def most_similar_script(script: str, prior_scripts: List[str]):
    """Returns (best_ratio, best_matching_script) against a list of prior scripts of the same content type."""
    best_ratio, best_script = 0.0, None
    for prior in prior_scripts:
        ratio = script_similarity_ratio(script, prior)
        if ratio > best_ratio:
            best_ratio, best_script = ratio, prior
    return best_ratio, best_script
