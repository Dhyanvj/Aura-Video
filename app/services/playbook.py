"""
Playbook distillation and versioning (docs/DECISIONS_V3.md §3). Raw
LessonLearned rows are never injected into a prompt directly - only a
distilled, human-reviewable Playbook (<=15 bullets) is, and only its active
version.
"""

from typing import List, Optional

from loguru import logger
from sqlmodel import select

from app.agents.base import BaseAgent
from app.agents.schemas import PlaybookBullet, PlaybookDistillation
from app.db import session_scope
from app.db.models import LessonLearned, Playbook
from app.utils import utils

# "weekly or every 10 projects, whichever first" (docs/DECISIONS_V3.md §3).
# The count-based half of that is checked deterministically after every
# retrospective; the weekly half is a scheduler job (app/services/scheduler.py)
# so a playbook still gets refreshed even during a quiet week with <10 new
# lessons.
DISTILLATION_LESSON_INTERVAL = 10


def record_lessons(project_id: int, content_type_id: Optional[str], lessons: list) -> None:
    with session_scope() as session:
        for lesson in lessons:
            session.add(
                LessonLearned(
                    project_id=project_id,
                    agent=lesson.agent,
                    content_type_id=content_type_id,
                    what_worked=lesson.what_worked,
                    what_failed=lesson.what_failed,
                    actionable_rule=lesson.actionable_rule,
                )
            )
        session.commit()


def lesson_count(agent: str, content_type_id: Optional[str]) -> int:
    with session_scope() as session:
        query = select(LessonLearned).where(LessonLearned.agent == agent)
        query = query.where(LessonLearned.content_type_id == content_type_id) if content_type_id else query.where(
            LessonLearned.content_type_id.is_(None)
        )
        return len(session.exec(query).all())


def is_distillation_due(agent: str, content_type_id: Optional[str]) -> bool:
    count = lesson_count(agent, content_type_id)
    return count > 0 and count % DISTILLATION_LESSON_INTERVAL == 0


def get_active_playbook(agent: str, content_type_id: Optional[str]) -> Optional[Playbook]:
    with session_scope() as session:
        query = select(Playbook).where(Playbook.agent == agent).where(Playbook.is_active.is_(True))
        query = query.where(Playbook.content_type_id == content_type_id) if content_type_id else query.where(
            Playbook.content_type_id.is_(None)
        )
        return session.exec(query).first()


def get_active_bullets(agent: str, content_type_id: Optional[str]) -> List[str]:
    """The only read path a live agent prompt should use - enabled bullets of the current active version."""
    playbook = get_active_playbook(agent, content_type_id)
    if playbook is None:
        return []
    return [b["text"] for b in playbook.bullets if b.get("enabled", True)]


def list_versions(agent: str, content_type_id: Optional[str]) -> List[Playbook]:
    with session_scope() as session:
        query = select(Playbook).where(Playbook.agent == agent)
        query = query.where(Playbook.content_type_id == content_type_id) if content_type_id else query.where(
            Playbook.content_type_id.is_(None)
        )
        return session.exec(query.order_by(Playbook.version.desc())).all()


def list_all_playbooks() -> List[Playbook]:
    """Active version of every (agent, content_type) pair that has one - for the Settings overview."""
    with session_scope() as session:
        return session.exec(select(Playbook).where(Playbook.is_active.is_(True))).all()


def _next_version(agent: str, content_type_id: Optional[str]) -> int:
    versions = list_versions(agent, content_type_id)
    return (versions[0].version + 1) if versions else 1


def _deactivate_all(session, agent: str, content_type_id: Optional[str]) -> None:
    query = select(Playbook).where(Playbook.agent == agent).where(Playbook.is_active.is_(True))
    query = query.where(Playbook.content_type_id == content_type_id) if content_type_id else query.where(
        Playbook.content_type_id.is_(None)
    )
    for row in session.exec(query).all():
        row.is_active = False
        session.add(row)


def create_version(agent: str, content_type_id: Optional[str], bullets: list) -> Playbook:
    with session_scope() as session:
        _deactivate_all(session, agent, content_type_id)
        playbook = Playbook(
            agent=agent,
            content_type_id=content_type_id,
            version=_next_version(agent, content_type_id),
            bullets=bullets,
            is_active=True,
        )
        session.add(playbook)
        session.commit()
        session.refresh(playbook)
        return playbook


def rollback_to(playbook_id: int) -> Playbook:
    """Reactivates a prior version verbatim - never deleted, so this is always available."""
    with session_scope() as session:
        target = session.get(Playbook, playbook_id)
        if target is None:
            raise ValueError(f"playbook {playbook_id} not found")
        _deactivate_all(session, target.agent, target.content_type_id)
        target.is_active = True
        session.add(target)
        session.commit()
        session.refresh(target)
        return target


def update_bullet(playbook_id: int, bullet_index: int, **fields) -> Playbook:
    """
    A human edit (toggle enabled, edit text) from Settings. Creates a new
    version with that one bullet changed, rather than mutating the row in
    place, so this shows up in version history like any other change.
    """
    with session_scope() as session:
        current = session.get(Playbook, playbook_id)
        if current is None:
            raise ValueError(f"playbook {playbook_id} not found")
        bullets = [dict(b) for b in current.bullets]
        if not (0 <= bullet_index < len(bullets)):
            raise ValueError(f"bullet index {bullet_index} out of range")
        bullets[bullet_index].update(fields)
        agent, content_type_id = current.agent, current.content_type_id

    return create_version(agent, content_type_id, bullets)


_DISTILL_SYSTEM_PROMPT = """You curate a compounding "what works" playbook for one AI agent in a short-form
video pipeline, for one content type. You're given every lesson recorded from past project retrospectives for
this agent+content type, and (if one already exists) the current playbook.

Produce at most 15 bullets: concrete, actionable instructions this agent's prompt can follow directly (not
vague advice). Merge near-duplicate lessons into one bullet. Prefer lessons that are specific to this content
type over generic advice. If the current playbook already has a good bullet, keep it (same text) rather than
rewording it for no reason - stable playbooks are easier for a human to review over time."""


def distill_playbook(agent: str, content_type_id: Optional[str]) -> Optional[Playbook]:
    with session_scope() as session:
        query = select(LessonLearned).where(LessonLearned.agent == agent)
        query = query.where(LessonLearned.content_type_id == content_type_id) if content_type_id else query.where(
            LessonLearned.content_type_id.is_(None)
        )
        lessons = session.exec(query.order_by(LessonLearned.created_at.asc())).all()
        lesson_payload = [
            {"id": lesson.id, "what_worked": lesson.what_worked, "what_failed": lesson.what_failed, "actionable_rule": lesson.actionable_rule}
            for lesson in lessons
        ]

    if not lesson_payload:
        return None

    current = get_active_playbook(agent, content_type_id)
    payload = {
        "agent": agent,
        "content_type_id": content_type_id,
        "lessons": lesson_payload,
        "current_playbook": current.bullets if current else [],
    }

    judge = BaseAgent()
    judge.agent_name = "playbook_distiller"
    judge.model = "claude-haiku-4-5"
    try:
        result: PlaybookDistillation = judge.call_json(
            system=_DISTILL_SYSTEM_PROMPT, user=utils.to_json(payload), response_model=PlaybookDistillation
        )
    except Exception as exc:  # noqa: BLE001 - distillation is best-effort background maintenance, never fatal
        logger.warning(f"playbook distillation failed for agent={agent} content_type={content_type_id}: {exc}")
        return None

    bullets = [PlaybookBullet(**b.model_dump()).model_dump() for b in result.bullets]
    return create_version(agent, content_type_id, bullets)
