"""
Failed-project rescue (manual override): when a Failed project's final
rendered video actually exists and is technically sound, a human can move it
straight into the normal human-review flow instead of forcing a re-render
from scratch. Covers two situations now that QA findings route to
NEEDS_HUMAN_REVIEW instead of Failed (see app/agents/quality_reviewer.py /
orchestrator.py's escalation redesign): (a) legacy projects marked Failed
under the old pre-redesign logic whose renders still exist, and (b)
pipeline-error failures where the final render nonetheless survived (a
post-render step - thumbnail generation, storyboard update, publish-prep -
crashed after a good final-video.mp4 was already written).

This module only *computes and reports* rescuability - it never mutates a
project's status. The actual state transition is a real, tested orchestrator
action (orchestrator.rescue_failed_project) so every existing invariant
(gates, revision budgets, override policy) still applies from review onward.
Nothing here ever auto-rescues anything.
"""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from loguru import logger
from sqlmodel import select

from app.db import session_scope
from app.db.models import ProjectStatus, VideoProject, utcnow
from app.services import audio_analysis, project_storage
from app.services import qa as qa_service

_REVISION_TIMESTAMP_RE = re.compile(r"^\d{8}T\d{6}$")


@dataclass
class RenderCandidate:
    id: str  # "current" | "revision:{timestamp}" | "recorded" - stable, never a raw filesystem path
    video_path: str
    label: str
    recorded_at: Optional[datetime] = None


@dataclass
class RescueEligibility:
    eligible: bool
    reason: str = ""
    # Only the technically-valid candidates, newest first. candidates[0] (if
    # any) is the default choice; the confirmation dialog may offer the rest.
    candidates: List[RenderCandidate] = field(default_factory=list)

    @property
    def best(self) -> Optional[RenderCandidate]:
        return self.candidates[0] if self.candidates else None


def _parse_revision_timestamp(name: str) -> Optional[datetime]:
    if not _REVISION_TIMESTAMP_RE.match(name):
        return None
    try:
        return datetime.strptime(name, "%Y%m%dT%H%M%S")
    except ValueError:
        return None


def list_render_candidates(project: VideoProject) -> List[RenderCandidate]:
    """
    Every render file this project has ever produced, newest first, deduped
    by real path: the current project-folder final-video.mp4, every
    revisions/{timestamp}/final-video.mp4 (project_storage.py's revision
    archive), and - for legacy/task-only projects, or if materialization
    never ran - project.video_path itself as a last resort. Does not check
    technical validity; see check_render_technically_valid for that.
    """
    seen_real_paths = set()
    candidates: List[RenderCandidate] = []

    abs_dir = project_storage.project_abs_dir(project.id)
    if abs_dir:
        current = os.path.join(abs_dir, "final-video.mp4")
        if os.path.isfile(current):
            seen_real_paths.add(os.path.realpath(current))
            candidates.append(RenderCandidate(id="current", video_path=current, label="current render"))

        revisions_dir = os.path.join(abs_dir, "revisions")
        if os.path.isdir(revisions_dir):
            dated = []
            for name in os.listdir(revisions_dir):
                timestamp = _parse_revision_timestamp(name)
                candidate_path = os.path.join(revisions_dir, name, "final-video.mp4")
                if timestamp is not None and os.path.isfile(candidate_path):
                    dated.append((timestamp, candidate_path))
            dated.sort(key=lambda entry: entry[0], reverse=True)
            for timestamp, path in dated:
                real = os.path.realpath(path)
                if real in seen_real_paths:
                    continue
                seen_real_paths.add(real)
                candidates.append(
                    RenderCandidate(
                        id=f"revision:{timestamp.strftime('%Y%m%dT%H%M%S')}",
                        video_path=path,
                        label=f"revision from {timestamp.strftime('%Y-%m-%d %H:%M')} UTC",
                        recorded_at=timestamp,
                    )
                )

    if project.video_path and os.path.isfile(project.video_path):
        real = os.path.realpath(project.video_path)
        if real not in seen_real_paths:
            seen_real_paths.add(real)
            candidates.append(RenderCandidate(id="recorded", video_path=project.video_path, label="last recorded render"))

    return candidates


def check_render_technically_valid(video_path: str) -> tuple:
    """
    (ok, reason). The same non-negotiable deterministic gates QA enforces on
    every render, re-run fresh here rather than trusted from any cached flag:
    file not corrupt/unreadable, duration in the platform range, correct
    resolution, and - going further than a routine technical pass - an
    audible, non-silent audio track. This is the gate "Mark as Successful"
    can never bypass; user convenience does not outrank it.
    """
    checks, _duration = qa_service.run_technical_checks(video_path)
    failed = [c for c in checks if not c.passed]
    if failed:
        return False, "; ".join(f"{c.name}: {c.detail}" for c in failed)
    audible, reason = audio_analysis.check_audible(video_path)
    if not audible:
        return False, reason
    return True, ""


def evaluate_rescuability(project: VideoProject) -> RescueEligibility:
    """
    The single source of truth for "can this Failed project be rescued right
    now" - always computed fresh from the files on disk. Callers that only
    need a cheap, possibly-stale summary for list/badge display should read
    the cached VideoProject.rescue_* columns (see store_rescuability)
    instead of calling this on every row.
    """
    candidates = list_render_candidates(project)
    if not candidates:
        return RescueEligibility(eligible=False, reason="no rendered video file exists for this project")

    valid: List[RenderCandidate] = []
    last_reason = ""
    for candidate in candidates:
        ok, reason = check_render_technically_valid(candidate.video_path)
        if ok:
            valid.append(candidate)
        else:
            last_reason = reason

    if not valid:
        return RescueEligibility(eligible=False, reason=last_reason or "no technically valid render found")

    return RescueEligibility(eligible=True, candidates=valid)


def resolve_candidate(project: VideoProject, candidate_id: Optional[str]) -> Optional[RenderCandidate]:
    """
    Looks up a candidate by the stable id the eligibility check handed the
    client - never trusts a client-supplied filesystem path directly. The
    candidate list is always regenerated server-side here, so an id that no
    longer resolves to anything (e.g. the file was removed between the
    eligibility check and the override click) simply returns None.
    """
    eligibility = evaluate_rescuability(project)
    if candidate_id is None:
        return eligibility.best
    return next((c for c in eligibility.candidates if c.id == candidate_id), None)


def has_script_edit(project: VideoProject) -> bool:
    """
    Best-effort "the rescued render may predate a later script edit" signal
    - human_edits entries don't carry timestamps, so this can't precisely
    order the edit against the render; it's a conservative catch-all note
    for the rescue banner, never a block (per spec: "note it... rather than
    blocking").
    """
    return any(edit.get("field") == "script" for edit in (project.human_edits or []))


def store_rescuability(project_id: int, eligibility: RescueEligibility) -> None:
    """Persists a computed eligibility result for cheap list/badge display. Never the authorization source of truth."""
    best = eligibility.best
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        if project is None:
            return
        project.rescue_eligible = eligibility.eligible
        project.rescue_checked_at = utcnow()
        project.rescue_ineligible_reason = None if eligibility.eligible else eligibility.reason
        project.rescue_candidate_path = best.video_path if best else None
        project.rescue_candidate_label = best.label if best else None
        session.add(project)
        session.commit()


def backfill_scan() -> dict:
    """
    One-time (or re-runnable, from Settings -> maintenance) scan of every
    Failed project: computes and stores rescuability so the Failed list can
    show a badge without an ffprobe call per row. Surfacing only - never
    transitions a project's status.
    """
    with session_scope() as session:
        failed_ids = session.exec(
            select(VideoProject.id).where(VideoProject.status == ProjectStatus.FAILED.value)
        ).all()

    scanned = 0
    eligible_count = 0
    for project_id in failed_ids:
        with session_scope() as session:
            project = session.get(VideoProject, project_id)
            if project is None:
                continue
        try:
            eligibility = evaluate_rescuability(project)
        except Exception as exc:  # noqa: BLE001 - one project's probe failure must not abort the whole scan
            logger.warning(f"rescue backfill scan: project {project_id} eligibility check failed: {exc}")
            continue
        store_rescuability(project_id, eligibility)
        scanned += 1
        if eligibility.eligible:
            eligible_count += 1

    logger.info(f"rescue backfill scan: {scanned} Failed project(s) checked, {eligible_count} rescuable")
    return {"scanned": scanned, "eligible": eligible_count}
