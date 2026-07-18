"""
Recycle Bin (docs/DECISIONS_V3.md): soft delete by default, a scheduled purge
job for anything past [storage].recycle_bin_retention_days, and a hard
safety rule for the one genuinely dangerous operation here - permanently
removing a project's folder from disk.

Safety rule (non-negotiable, see file_security.resolve_directory_for_deletion):
filesystem deletion only ever resolves a project ID -> its canonical folder
via the DB (VideoProject.storage_path / task_id), then verifies the resolved
real path is strictly inside storage/projects/ or storage/tasks/ before
removal. A path is never accepted from the client.

Originality-fingerprint rule (docs/DECISIONS_V3.md §2, spelled out here per
the product spec): permanently deleting a project purges its TopicEmbedding/
UsedFact rows ONLY if it was never published (VideoProject.published_at is
None) - so a bad attempt can be retried under the same topic. A project that
WAS published keeps its fingerprint forever, even after permanent deletion,
so the dedupe engine can never let a later project recreate content that's
already live. See _purge_or_keep_fingerprints below; both paths are covered
in test_project_deletion.py.
"""

import os
import shutil
import time
from datetime import timedelta
from typing import Optional

from loguru import logger
from sqlmodel import select

from app.config import config
from app.db import session_scope
from app.db.models import (
    AgentEvent,
    LessonLearned,
    ProjectClip,
    ProjectStatus,
    Series,
    TopicEmbedding,
    UsedFact,
    VideoProject,
    utcnow,
)
from app.services import cancellation, project_storage
from app.services.ws_manager import broadcast_status
from app.utils import file_security, utils


def _in_flight_statuses() -> set:
    # Reuses orchestrator._RESUMABLE_STATUSES (states a crash-restart
    # resumes - i.e. exactly the states where a background thread may
    # currently be doing work) plus PUBLISHING, which isn't resumable but is
    # still in-flight. Imported lazily to avoid a circular import at module
    # load time (app.agents.orchestrator imports several app.services
    # modules itself).
    from app.agents import orchestrator

    return orchestrator._RESUMABLE_STATUSES | {ProjectStatus.PUBLISHING.value}


_CANCEL_WAIT_TIMEOUT_S = 30.0
_CANCEL_POLL_INTERVAL_S = 0.2


def _retention_days() -> int:
    return int(config.storage.get("recycle_bin_retention_days", 7))


class ProjectNotFoundError(ValueError):
    pass


class ProjectNotDeletedError(PermissionError):
    """Raised when restore/purge is attempted on a project that isn't in the bin."""


class ProjectAlreadyDeletedError(PermissionError):
    """Raised when a soft-delete is attempted on a project already in the bin."""


def _get_or_raise(session, project_id: int) -> VideoProject:
    project = session.get(VideoProject, project_id)
    if project is None:
        raise ProjectNotFoundError(f"project {project_id} not found")
    return project


def _wait_for_in_flight_to_stop(project_id: int, timeout: Optional[float] = None) -> None:
    """
    Signals cancellation and blocks until the pipeline thread actually
    stops (reaches CANCELLED, or any other terminal/non-in-flight status -
    a checkpoint can also legitimately finish into FAILED, e.g. if the
    cancellation raced a real failure). Raises TimeoutError rather than
    proceeding, so a caller never soft-deletes out from under a render
    that's still writing into the project's folder.

    timeout defaults to the module-level _CANCEL_WAIT_TIMEOUT_S, looked up
    here (not as a parameter default) so tests can patch.object() it.
    """
    if timeout is None:
        timeout = _CANCEL_WAIT_TIMEOUT_S
    cancellation.request_cancel(project_id)
    in_flight = _in_flight_statuses()
    deadline = time.time() + timeout
    while time.time() < deadline:
        with session_scope() as session:
            project = session.get(VideoProject, project_id)
            if project is None or project.status not in in_flight:
                return
        time.sleep(_CANCEL_POLL_INTERVAL_S)
    raise TimeoutError(
        f"project {project_id} did not stop within {timeout}s of a cancellation request; not deleting"
    )


def _middle_episode_warning(session, project: VideoProject) -> Optional[str]:
    if not project.series_id or project.episode_number is None:
        return None
    series = session.get(Series, project.series_id)
    if series is None or series.status != "active":
        return None
    later_episode_exists = session.exec(
        select(VideoProject.id)
        .where(VideoProject.series_id == project.series_id)
        .where(VideoProject.episode_number > project.episode_number)
        .where(VideoProject.status != ProjectStatus.DELETED.value)
    ).first()
    if later_episode_exists:
        return (
            f"Episode {project.episode_number} is not the latest episode of an active series; "
            "deleting it will renumber how the Series Bible summarizes remaining episodes."
        )
    return None


def _recompute_series_summary(session, series_id: int) -> None:
    """
    Rebuilds Series.rolling_summary from whatever episodes are still not
    DELETED, so a soft-deleted (or purged) middle episode's "previously on"
    line disappears from future scripts. episode_counter is deliberately
    left untouched - it's a monotonic reservation for the *next* episode
    number (orchestrator.next_episode_number); rewinding it after a deletion
    would risk a future episode colliding with a number that's still visibly
    in use by a surviving episode.
    """
    series = session.get(Series, series_id)
    if series is None:
        return
    remaining = session.exec(
        select(VideoProject)
        .where(VideoProject.series_id == series_id)
        .where(VideoProject.status != ProjectStatus.DELETED.value)
        .where(VideoProject.topic.is_not(None))
        .order_by(VideoProject.episode_number.asc())
    ).all()
    lines = [f"Episode {p.episode_number}: {p.topic}" for p in remaining if p.episode_number]
    series.rolling_summary = "\n".join(lines[-5:])
    series.updated_at = utcnow()
    session.add(series)
    session.commit()


def delete_project(project_id: int, permanent: bool = False) -> dict:
    """
    The single entry point for both the soft-delete (Recycle Bin) and
    permanent-delete actions. In-flight projects are cancelled and awaited
    first (see _wait_for_in_flight_to_stop) so nothing is ever soft-deleted
    out from under a still-running render.
    """
    with session_scope() as session:
        project = _get_or_raise(session, project_id)
        current_status = project.status
        warning = _middle_episode_warning(session, project)
        was_published = project.published_at is not None

    if current_status == ProjectStatus.DELETED.value and not permanent:
        raise ProjectAlreadyDeletedError(f"project {project_id} is already deleted")

    if current_status in _in_flight_statuses():
        _wait_for_in_flight_to_stop(project_id)

    if permanent or _retention_days() <= 0:
        result = purge_project(project_id, require_deleted=False)
        result["warning"] = warning
        result["was_published"] = was_published
        return result

    with session_scope() as session:
        project = _get_or_raise(session, project_id)
        project.status_before_delete = project.status
        project.status = ProjectStatus.DELETED.value
        project.deleted_at = utcnow()
        session.add(project)
        session.commit()
        series_id = project.series_id

    if series_id:
        with session_scope() as session:
            _recompute_series_summary(session, series_id)

    broadcast_status(project_id, ProjectStatus.DELETED.value)
    logger.info(f"project {project_id} moved to Recycle Bin (was {current_status})")
    return {"project_id": project_id, "permanent": False, "warning": warning, "was_published": was_published}


def restore_project(project_id: int) -> dict:
    with session_scope() as session:
        project = _get_or_raise(session, project_id)
        if project.status != ProjectStatus.DELETED.value:
            raise ProjectNotDeletedError(f"project {project_id} is not in the Recycle Bin")
        restored_status = project.status_before_delete or ProjectStatus.FAILED.value
        project.status = restored_status
        project.status_before_delete = None
        project.deleted_at = None
        session.add(project)
        session.commit()
        series_id = project.series_id

    if series_id:
        with session_scope() as session:
            _recompute_series_summary(session, series_id)

    broadcast_status(project_id, restored_status)
    logger.info(f"project {project_id} restored from Recycle Bin to {restored_status}")
    return {"project_id": project_id, "status": restored_status}


def _purge_or_keep_fingerprints(session, project_id: int, was_published: bool) -> None:
    # See the module docstring's "Originality-fingerprint rule": keep the
    # fingerprint forever for anything that was ever published, so the
    # dedupe engine can never let the system recreate content that's already
    # live; purge it otherwise, so a bad attempt can be retried.
    if was_published:
        return
    for row in session.exec(select(TopicEmbedding).where(TopicEmbedding.project_id == project_id)).all():
        session.delete(row)
    for row in session.exec(select(UsedFact).where(UsedFact.project_id == project_id)).all():
        session.delete(row)
    session.commit()


def _safe_rmtree(root_dir: str, relative_dir: Optional[str], label: str, project_id: int, *, must_be_within: str) -> bool:
    """
    Resolves relative_dir against root_dir (the broad storage/ directory -
    VideoProject.storage_path is stored relative to storage/, already
    including its own "projects/..." prefix) and only removes it if the
    resolved real path is ALSO strictly inside must_be_within (storage/
    projects/ or storage/tasks/, per the safety rule) - narrower than what
    resolve_directory_for_deletion alone checks, and exactly the boundary
    the spec calls for.
    """
    if not relative_dir:
        return False
    try:
        resolved = file_security.resolve_directory_for_deletion(root_dir, relative_dir)
    except ValueError as exc:
        logger.error(f"refusing to delete {label} for project {project_id}: {exc}")
        return False

    boundary = os.path.realpath(must_be_within)
    if os.path.commonpath([boundary, resolved]) != boundary:
        logger.error(
            f"refusing to delete {label} for project {project_id}: "
            f"{resolved!r} resolves outside the required root {boundary!r}"
        )
        return False

    shutil.rmtree(resolved, ignore_errors=True)
    return True


def purge_project(project_id: int, require_deleted: bool = True) -> dict:
    """
    Permanently removes a project: its storage/projects/ (and, for
    pre-migration projects, legacy storage/tasks/{task_id}) folder, then its
    DB rows (VideoProject, AgentEvent, TopicEmbedding/UsedFact per the
    fingerprint rule, ProjectClip, LessonLearned). QA reports live inline on
    VideoProject.qa_reports, so no separate table to clean up there.
    """
    with session_scope() as session:
        project = _get_or_raise(session, project_id)
        if require_deleted and project.status != ProjectStatus.DELETED.value:
            raise ProjectNotDeletedError(f"project {project_id} must be soft-deleted before it can be purged")
        storage_path = project.storage_path
        task_id = project.task_id
        series_id = project.series_id
        was_published = project.published_at is not None

    folder_removed = _safe_rmtree(
        utils.storage_dir(), storage_path, "project folder", project_id,
        must_be_within=project_storage.projects_root(),
    )
    if task_id:
        _safe_rmtree(
            utils.storage_dir(), os.path.join("tasks", task_id), "legacy task folder", project_id,
            must_be_within=utils.storage_dir("tasks"),
        )

    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        if project is None:
            return {"project_id": project_id, "permanent": True, "folder_removed": folder_removed}

        _purge_or_keep_fingerprints(session, project_id, was_published)

        for model in (AgentEvent, ProjectClip, LessonLearned):
            for row in session.exec(select(model).where(model.project_id == project_id)).all():
                session.delete(row)

        session.delete(project)
        session.commit()

    if series_id:
        with session_scope() as session:
            _recompute_series_summary(session, series_id)

    logger.info(f"project {project_id} permanently purged (folder_removed={folder_removed})")
    return {
        "project_id": project_id,
        "permanent": True,
        "folder_removed": folder_removed,
        "freed_topic_for_reuse": not was_published,
    }


def bulk_delete(project_ids: list, permanent: bool = False) -> dict:
    results, errors = [], []
    for project_id in project_ids:
        try:
            results.append(delete_project(project_id, permanent=permanent))
        except Exception as exc:  # noqa: BLE001 - one project's failure must not block the rest of the batch
            logger.warning(f"bulk delete: project {project_id} failed: {exc}")
            errors.append({"project_id": project_id, "error": str(exc)})
    return {"deleted": results, "errors": errors}


def _folder_size_bytes(abs_dir: Optional[str]) -> int:
    if not abs_dir or not os.path.isdir(abs_dir):
        return 0
    total = 0
    for root, _dirs, files in os.walk(abs_dir):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def list_recycle_bin() -> list:
    retention_days = _retention_days()
    with session_scope() as session:
        rows = session.exec(
            select(VideoProject)
            .where(VideoProject.status == ProjectStatus.DELETED.value)
            .order_by(VideoProject.deleted_at.desc())
        ).all()
        items = []
        for project in rows:
            abs_dir = project_storage.project_abs_dir(project.id)
            deleted_at = project.deleted_at or utcnow()
            days_remaining = None
            if retention_days > 0:
                expires_at = deleted_at + timedelta(days=retention_days)
                days_remaining = max(0, (expires_at - utcnow()).days)
            thumbnail = os.path.join(abs_dir, "thumbnail.jpg") if abs_dir else None
            items.append(
                {
                    "id": project.id,
                    "topic": project.topic,
                    "status_before_delete": project.status_before_delete,
                    "deleted_at": deleted_at.isoformat(),
                    "days_remaining": days_remaining,
                    "size_bytes": _folder_size_bytes(abs_dir),
                    "has_thumbnail": bool(thumbnail and os.path.isfile(thumbnail)),
                    "was_published": project.published_at is not None,
                }
            )
    return items


def purge_expired() -> int:
    """Called by the scheduler. Permanently removes everything past retention."""
    retention_days = _retention_days()
    if retention_days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=retention_days)
    with session_scope() as session:
        expired_ids = session.exec(
            select(VideoProject.id)
            .where(VideoProject.status == ProjectStatus.DELETED.value)
            .where(VideoProject.deleted_at <= cutoff)
        ).all()
    purged = 0
    for project_id in expired_ids:
        try:
            purge_project(project_id)
            purged += 1
        except Exception as exc:  # noqa: BLE001 - one project's failure must not block the rest of the sweep
            logger.warning(f"recycle bin purge: project {project_id} failed: {exc}")
    return purged
