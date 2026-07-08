import os
from typing import List, Literal, Optional

from fastapi import Path, Request
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel
from sqlmodel import select

from app.agents import orchestrator
from app.config import config
from app.controllers import base
from app.controllers.v1.base import new_router
from app.db import session_scope
from app.db.models import AgentEvent, ContentTypeTemplate, ProjectStatus, VideoProject
from app.models.exception import HttpException
from app.services import project_deletion, project_storage
from app.utils import file_security, utils

router = new_router()


class CreateProjectRequest(BaseModel):
    # Leave topic empty to have the Trend Scout pick one automatically for
    # niche/audience (falling back to the configured defaults in [trends]).
    topic: Optional[str] = ""
    niche: Optional[str] = ""
    audience: Optional[str] = ""
    content_type_id: Optional[str] = None
    quality_preset: Optional[str] = None
    # "none" (default, one-off video), "new" (create a series, this becomes
    # episode 1 - series_title required), or "continue" (add the next
    # episode to an existing series - series_id required).
    series_mode: Literal["none", "new", "continue"] = "none"
    series_title: Optional[str] = None
    series_id: Optional[int] = None
    # Per-project override of the Settings-level Approval Mode (New Video
    # flow's "override for this project" toggle). None = use the current
    # Settings value, snapshotted onto the project at creation either way.
    approval_mode_override: Optional[Literal["manual", "automatic"]] = None


class RejectProjectRequest(BaseModel):
    revision_notes: str


class ApproveProjectRequest(BaseModel):
    platforms: List[str]
    thumbnail_path: Optional[str] = None


class PlatformUrl(BaseModel):
    platform: str
    url: Optional[str] = None


class MarkPublishedRequest(BaseModel):
    platform_urls: List[PlatformUrl] = []


class UpdateMetadataRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None


class RegenerateScriptRequest(BaseModel):
    notes: Optional[str] = ""


class RejectScriptRequest(BaseModel):
    notes: Optional[str] = ""


class UpdateScriptRequest(BaseModel):
    title: Optional[str] = None
    script: Optional[str] = None


@router.post("/projects", summary="Start a new agent-driven video project")
def create_project(request: Request, body: CreateProjectRequest):
    topic = (body.topic or "").strip()
    niche = (body.niche or "").strip() or config.trends.get("niche", "")

    if body.content_type_id is not None:
        with session_scope() as session:
            if session.get(ContentTypeTemplate, body.content_type_id) is None:
                raise HttpException(
                    task_id="", status_code=404, message=f"content type {body.content_type_id!r} not found"
                )

    series_id, episode_number = _resolve_series(body)

    if topic:
        project_id = orchestrator.start_manual_project(
            topic=topic,
            niche=niche,
            content_type_id=body.content_type_id,
            quality_preset=body.quality_preset,
            series_id=series_id,
            episode_number=episode_number,
            approval_mode_override=body.approval_mode_override,
        )
    else:
        audience = (body.audience or "").strip() or config.trends.get("audience", "")
        project_id = orchestrator.start_auto_trend_project(
            niche=niche,
            audience=audience,
            content_type_id=body.content_type_id,
            quality_preset=body.quality_preset,
            series_id=series_id,
            episode_number=episode_number,
            approval_mode_override=body.approval_mode_override,
        )
    return utils.get_response(200, {"project_id": project_id})


def _resolve_series(body: CreateProjectRequest) -> tuple:
    if body.series_mode == "none":
        return None, None

    if body.series_mode == "new":
        title = (body.series_title or "").strip()
        if not title:
            raise HttpException(task_id="", status_code=400, message="series_title is required for series_mode='new'")
        content_type_id = body.content_type_id or "motivational"
        series_id = orchestrator.create_series(content_type_id, title)
        return series_id, orchestrator.next_episode_number(series_id)

    # series_mode == "continue"
    if body.series_id is None:
        raise HttpException(task_id="", status_code=400, message="series_id is required for series_mode='continue'")
    with session_scope() as session:
        from app.db.models import Series

        if session.get(Series, body.series_id) is None:
            raise HttpException(task_id="", status_code=404, message=f"series {body.series_id} not found")
    return body.series_id, orchestrator.next_episode_number(body.series_id)


@router.post("/projects/{project_id}/approve", summary="Approve a project and publish it to the given platforms")
def approve_project(request: Request, body: ApproveProjectRequest, project_id: int = Path(...)):
    # Platforms are only required while publishing is actually enabled - with
    # it paused, approving just marks the project complete (see
    # orchestrator.approve_and_publish).
    if config.features.get("publishing_enabled", False) and not body.platforms:
        raise HttpException(task_id="", status_code=400, message="platforms must not be empty")
    try:
        orchestrator.approve_and_publish(project_id, body.platforms, body.thumbnail_path)
    except ValueError as exc:
        raise HttpException(task_id="", status_code=404, message=str(exc))
    except PermissionError as exc:
        raise HttpException(task_id="", status_code=409, message=str(exc))
    except RuntimeError as exc:
        raise HttpException(task_id="", status_code=400, message=str(exc))
    return utils.get_response(200, {"project_id": project_id})


@router.post("/projects/{project_id}/reject", summary="Request changes with notes, triggering a revision")
def reject_project(request: Request, body: RejectProjectRequest, project_id: int = Path(...)):
    if not body.revision_notes.strip():
        raise HttpException(task_id="", status_code=400, message="revision_notes must not be empty")
    with session_scope() as session:
        if session.get(VideoProject, project_id) is None:
            raise HttpException(task_id="", status_code=404, message=f"project {project_id} not found")
    orchestrator.retry_with_revision(project_id, body.revision_notes.strip())
    return utils.get_response(200, {"project_id": project_id})


@router.post("/projects/{project_id}/retry", summary="Retry a failed project from scratch (no revision notes)")
def retry_project(request: Request, project_id: int = Path(...)):
    try:
        orchestrator.retry_failed_project(project_id)
    except ValueError as exc:
        raise HttpException(task_id="", status_code=404, message=str(exc))
    except PermissionError as exc:
        raise HttpException(task_id="", status_code=409, message=str(exc))
    return utils.get_response(200, {"project_id": project_id})


@router.post(
    "/projects/{project_id}/mark-published",
    summary="Record that an approved project was posted manually while publishing is frozen",
)
def mark_published(request: Request, body: MarkPublishedRequest, project_id: int = Path(...)):
    try:
        orchestrator.mark_as_published(project_id, [p.model_dump() for p in body.platform_urls])
    except ValueError as exc:
        raise HttpException(task_id="", status_code=404, message=str(exc))
    except PermissionError as exc:
        raise HttpException(task_id="", status_code=409, message=str(exc))
    return utils.get_response(200, {"project_id": project_id})


def _script_gate_error(exc: Exception) -> HttpException:
    if isinstance(exc, ValueError):
        return HttpException(task_id="", status_code=404, message=str(exc))
    if isinstance(exc, PermissionError):
        return HttpException(task_id="", status_code=409, message=str(exc))
    if isinstance(exc, RuntimeError):
        return HttpException(task_id="", status_code=400, message=str(exc))
    raise exc


@router.post(
    "/projects/{project_id}/approve-script",
    summary="Approve the script at the AWAITING_SCRIPT_APPROVAL gate; production begins",
)
def approve_script(request: Request, project_id: int = Path(...)):
    try:
        orchestrator.approve_script(project_id)
    except (ValueError, PermissionError, RuntimeError) as exc:
        raise _script_gate_error(exc)
    return utils.get_response(200, {"project_id": project_id})


@router.post(
    "/projects/{project_id}/reject-script",
    summary="Reject the topic at the script-approval gate; returns to idea stage",
)
def reject_script(request: Request, body: RejectScriptRequest, project_id: int = Path(...)):
    try:
        orchestrator.reject_topic_at_script(project_id, (body.notes or "").strip())
    except (ValueError, PermissionError, RuntimeError) as exc:
        raise _script_gate_error(exc)
    return utils.get_response(200, {"project_id": project_id})


@router.post(
    "/projects/{project_id}/regenerate-script",
    summary="Regenerate the script with notes at the approval gate (capped, tracked separately from QA revisions)",
)
def regenerate_script(request: Request, body: RegenerateScriptRequest, project_id: int = Path(...)):
    try:
        orchestrator.regenerate_script(project_id, (body.notes or "").strip())
    except (ValueError, PermissionError, RuntimeError) as exc:
        raise _script_gate_error(exc)
    return utils.get_response(200, {"project_id": project_id})


@router.patch(
    "/projects/{project_id}/script",
    summary="Autosave a title/script edit at the script-approval gate; re-syncs search terms and records a human-edit diff",
)
def update_script(request: Request, body: UpdateScriptRequest, project_id: int = Path(...)):
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        if project is None:
            raise HttpException(task_id="", status_code=404, message=f"project {project_id} not found")
        if project.status != ProjectStatus.AWAITING_SCRIPT_APPROVAL.value:
            raise HttpException(
                task_id="", status_code=409,
                message=f"project {project_id} is not awaiting script approval (status={project.status})",
            )
        brief = dict(project.brief or {})
        edits = list(project.human_edits or [])
        script_changed = False

        if body.title is not None:
            metadata_draft = dict(brief.get("metadata_draft") or {})
            original_title = metadata_draft.get("working_title", "")
            edits = _record_human_edit(edits, "title", original_title, body.title)
            metadata_draft["working_title"] = body.title
            brief["metadata_draft"] = metadata_draft
        if body.script is not None:
            original_script = brief.get("script", "")
            edits = _record_human_edit(edits, "script", original_script, body.script)
            script_changed = body.script != original_script
            brief["script"] = body.script

        project.brief = brief
        project.human_edits = edits
        session.add(project)
        session.commit()

    if script_changed:
        # Re-syncs the scene plan (search terms) to the edited script in the
        # background, without re-running topic research - see
        # orchestrator.resync_scene_plan.
        orchestrator.resync_scene_plan(project_id)
    return utils.get_response(200, {"project_id": project_id})


def _record_human_edit(edits: list, field: str, original_value: str, new_value: str) -> list:
    """
    Upserts one {field, before, after} entry per field rather than appending
    on every autosave call - autosave fires per debounce tick while typing,
    so a naive append would flood human_edits with keystroke-by-keystroke
    noise instead of the one clean "AI drafted X, human corrected to Y" diff
    the retrospective (docs/DECISIONS_V3.md §3) actually wants. `before`
    always stays pinned to the first-seen (agent-drafted) value; `after`
    tracks the latest edit; the entry is dropped entirely if the human types
    their way back to the original value.
    """
    existing = next((e for e in edits if e["field"] == field), None)
    before = existing["before"] if existing else original_value
    if before == new_value:
        return [e for e in edits if e["field"] != field]
    entry = {"field": field, "before": before, "after": new_value}
    return [entry if e["field"] == field else e for e in edits] if existing else edits + [entry]


@router.patch(
    "/projects/{project_id}/metadata",
    summary="Autosave a title/description edit at Final Review (UI v3 reduced-clicks)",
)
def update_metadata(request: Request, body: UpdateMetadataRequest, project_id: int = Path(...)):
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        if project is None:
            raise HttpException(task_id="", status_code=404, message=f"project {project_id} not found")
        package = dict(project.publish_package or {})
        edits = list(project.human_edits or [])

        if body.title is not None:
            title_options = list(package.get("title_options") or [""])
            original_title = title_options[0] if title_options else ""
            edits = _record_human_edit(edits, "title", original_title, body.title)
            title_options[0] = body.title
            package["title_options"] = title_options
        if body.description is not None:
            original_description = package.get("description", "")
            edits = _record_human_edit(edits, "description", original_description, body.description)
            package["description"] = body.description

        project.publish_package = package
        project.human_edits = edits
        session.add(project)
        session.commit()
    return utils.get_response(200, {"project_id": project_id})


@router.get("/projects", summary="List all projects (excludes the Recycle Bin)")
def get_all_projects(request: Request):
    with session_scope() as session:
        projects = session.exec(
            select(VideoProject)
            .where(VideoProject.status != ProjectStatus.DELETED.value)
            .order_by(VideoProject.created_at.desc())
        ).all()
        data = [_project_summary(p) for p in projects]
    return utils.get_response(200, {"projects": data})


@router.get("/projects/{project_id}", summary="Get full project detail including agent events")
def get_project(request: Request, project_id: int = Path(...)):
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        if project is None:
            raise HttpException(task_id="", status_code=404, message=f"project {project_id} not found")
        events = session.exec(
            select(AgentEvent).where(AgentEvent.project_id == project_id).order_by(AgentEvent.created_at.asc())
        ).all()
        data = _project_summary(project)
        data["events"] = [_event_summary(e) for e in events]
    return utils.get_response(200, data)


def _project_summary(project: VideoProject) -> dict:
    return {
        "id": project.id,
        "status": project.status,
        "niche": project.niche,
        "topic": project.topic,
        "trend_report": project.trend_report,
        "research_evidence": project.research_evidence,
        "brief": project.brief,
        "qa_reports": project.qa_reports,
        "publish_package": project.publish_package,
        "published_posts": project.published_posts,
        "task_id": project.task_id,
        "video_path": project.video_path,
        "storage_path": project.storage_path,
        "cost_usd": project.cost_usd,
        "revision_count": project.revision_count,
        "failure_reason": project.failure_reason,
        "content_type_id": project.content_type_id,
        "quality_preset": project.quality_preset,
        "series_id": project.series_id,
        "episode_number": project.episode_number,
        "approval_mode": project.approval_mode,
        "script_revision_count": project.script_revision_count,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


def _resolve_project_file_path(project_id: int, filename: str, request_id: str) -> str:
    # Anchored at this specific project's own folder (not the shared
    # storage/projects root) - narrower than the legacy task-id routes'
    # shared-root guard, so a manipulated filename can't reach another
    # project's files even by guessing its folder name.
    abs_dir = project_storage.project_abs_dir(project_id)
    if abs_dir is None:
        raise HttpException(task_id=request_id, status_code=404, message=f"project {project_id} has no storage folder")
    try:
        return file_security.resolve_path_within_directory(abs_dir, filename)
    except ValueError as exc:
        logger.warning(
            f"reject unsafe project file path, request_id: {request_id}, project_id: {project_id}, "
            f"path: {filename}, error: {exc}"
        )
        raise HttpException(
            task_id=request_id,
            status_code=404 if str(exc) == "file does not exist" else 403,
            message=f"{request_id}: invalid file path",
        )


@router.get("/projects/{project_id}/files/{filename:path}", summary="Download a file from a project's storage folder")
def get_project_file(request: Request, project_id: int = Path(...), filename: str = Path(...)):
    request_id = base.get_task_id(request)
    file_path = _resolve_project_file_path(project_id, filename, request_id)
    return FileResponse(path=file_path, filename=os.path.basename(file_path))


def _event_summary(event: AgentEvent) -> dict:
    return {
        "id": event.id,
        "agent": event.agent,
        "type": event.type,
        "message": event.message,
        "payload": event.payload,
        "tokens_in": event.tokens_in,
        "tokens_out": event.tokens_out,
        "cost_usd": event.cost_usd,
        "created_at": event.created_at.isoformat(),
    }


class DeleteProjectRequest(BaseModel):
    permanent: bool = False


class BulkDeleteRequest(BaseModel):
    project_ids: List[int]
    permanent: bool = False


def _deletion_error_response(exc: Exception) -> HttpException:
    if isinstance(exc, project_deletion.ProjectNotFoundError):
        return HttpException(task_id="", status_code=404, message=str(exc))
    if isinstance(exc, (project_deletion.ProjectAlreadyDeletedError, project_deletion.ProjectNotDeletedError)):
        return HttpException(task_id="", status_code=409, message=str(exc))
    if isinstance(exc, TimeoutError):
        return HttpException(task_id="", status_code=409, message=str(exc))
    return HttpException(task_id="", status_code=400, message=str(exc))


@router.post(
    "/projects/{project_id}/delete",
    summary="Soft-delete a project to the Recycle Bin (or permanently delete it)",
)
def delete_project(request: Request, body: DeleteProjectRequest, project_id: int = Path(...)):
    try:
        result = project_deletion.delete_project(project_id, permanent=body.permanent)
    except Exception as exc:  # noqa: BLE001 - narrowed to the right HTTP status below
        raise _deletion_error_response(exc)
    return utils.get_response(200, result)


@router.post("/projects/bulk-delete", summary="Delete multiple projects at once (e.g. clearing out failed runs)")
def bulk_delete_projects(request: Request, body: BulkDeleteRequest):
    result = project_deletion.bulk_delete(body.project_ids, permanent=body.permanent)
    return utils.get_response(200, result)


@router.get("/recycle-bin", summary="List soft-deleted projects")
def list_recycle_bin(request: Request):
    return utils.get_response(200, {"items": project_deletion.list_recycle_bin()})


@router.post("/recycle-bin/{project_id}/restore", summary="Restore a project from the Recycle Bin")
def restore_project(request: Request, project_id: int = Path(...)):
    try:
        result = project_deletion.restore_project(project_id)
    except Exception as exc:  # noqa: BLE001 - narrowed to the right HTTP status below
        raise _deletion_error_response(exc)
    return utils.get_response(200, result)


@router.post("/recycle-bin/{project_id}/purge", summary="Permanently delete a project already in the Recycle Bin")
def purge_project(request: Request, project_id: int = Path(...)):
    try:
        result = project_deletion.purge_project(project_id)
    except Exception as exc:  # noqa: BLE001 - narrowed to the right HTTP status below
        raise _deletion_error_response(exc)
    return utils.get_response(200, result)
