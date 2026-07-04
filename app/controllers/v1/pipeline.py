from typing import Optional

from fastapi import Path, Request
from pydantic import BaseModel
from sqlmodel import select

from app.agents import orchestrator
from app.controllers.v1.base import new_router
from app.db import session_scope
from app.db.models import AgentEvent, VideoProject
from app.models.exception import HttpException
from app.utils import utils

router = new_router()


class CreateProjectRequest(BaseModel):
    topic: str
    niche: Optional[str] = ""


@router.post("/projects", summary="Start a new agent-driven video project from a manual topic")
def create_project(request: Request, body: CreateProjectRequest):
    if not body.topic.strip():
        raise HttpException(task_id="", status_code=400, message="topic must not be empty")
    project_id = orchestrator.start_manual_project(topic=body.topic.strip(), niche=(body.niche or "").strip())
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
        "task_id": project.task_id,
        "video_path": project.video_path,
        "cost_usd": project.cost_usd,
        "revision_count": project.revision_count,
        "failure_reason": project.failure_reason,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


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
