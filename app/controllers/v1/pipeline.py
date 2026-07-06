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
from app.db.models import AgentEvent, ContentTypeTemplate, VideoProject
from app.models.exception import HttpException
from app.services import project_storage
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
        if body.title is not None:
            title_options = list(package.get("title_options") or [""])
            title_options[0] = body.title
            package["title_options"] = title_options
        if body.description is not None:
            package["description"] = body.description
        project.publish_package = package
        session.add(project)
        session.commit()
    return utils.get_response(200, {"project_id": project_id})


@router.get("/projects", summary="List all projects")
def get_all_projects(request: Request):
    with session_scope() as session:
        projects = session.exec(select(VideoProject).order_by(VideoProject.created_at.desc())).all()
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
