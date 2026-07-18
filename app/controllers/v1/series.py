from typing import Optional

from fastapi import Path, Request
from pydantic import BaseModel
from sqlmodel import select

from app.agents import orchestrator
from app.controllers.v1.base import new_router
from app.db import session_scope
from app.db.models import ContentTypeTemplate, Series, VideoProject
from app.models.exception import HttpException
from app.utils import utils

router = new_router()


class CreateSeriesRequest(BaseModel):
    content_type_id: str
    title: str
    style_guide: Optional[dict] = None


@router.post("/series", summary="Start a new series (its Bible starts with no locked voice yet)")
def create_series(request: Request, body: CreateSeriesRequest):
    with session_scope() as session:
        template = session.get(ContentTypeTemplate, body.content_type_id)
        if template is None:
            raise HttpException(
                task_id="", status_code=404, message=f"content type {body.content_type_id!r} not found"
            )
        if not template.enabled:
            raise HttpException(
                task_id="",
                status_code=400,
                message=f"content type {body.content_type_id!r} is disabled and cannot start a new series",
            )
    title = body.title.strip()
    if not title:
        raise HttpException(task_id="", status_code=400, message="title must not be empty")
    series_id = orchestrator.create_series(body.content_type_id, title, body.style_guide)
    return utils.get_response(200, {"series_id": series_id})


@router.get("/series", summary="List all series with their episode counts")
def list_series(request: Request):
    with session_scope() as session:
        rows = session.exec(select(Series).order_by(Series.created_at.desc())).all()
        data = [_series_summary(session, s) for s in rows]
    return utils.get_response(200, {"series": data})


@router.get("/series/{series_id}", summary="Series Bible detail plus its episode list")
def get_series(request: Request, series_id: int = Path(...)):
    with session_scope() as session:
        series = session.get(Series, series_id)
        if series is None:
            raise HttpException(task_id="", status_code=404, message=f"series {series_id} not found")
        data = _series_summary(session, series)
        episodes = session.exec(
            select(VideoProject)
            .where(VideoProject.series_id == series_id)
            .order_by(VideoProject.episode_number.asc())
        ).all()
        data["episodes"] = [_episode_summary(p) for p in episodes]
    return utils.get_response(200, data)


def _series_summary(session, series: Series) -> dict:
    episode_count = len(
        session.exec(select(VideoProject.id).where(VideoProject.series_id == series.id)).all()
    )
    return {
        "id": series.id,
        "content_type_id": series.content_type_id,
        "title": series.title,
        "style_guide": series.style_guide,
        "voice_id": series.voice_id,
        "voice_delivery_settings": series.voice_delivery_settings,
        "music_palette": series.music_palette,
        "character_reference": series.character_reference,
        "pronunciation_dictionary": series.pronunciation_dictionary,
        "episode_counter": series.episode_counter,
        "episode_count": episode_count,
        "rolling_summary": series.rolling_summary,
        "status": series.status,
        "created_at": series.created_at.isoformat(),
        "updated_at": series.updated_at.isoformat(),
    }


def _episode_summary(project: VideoProject) -> dict:
    return {
        "id": project.id,
        "episode_number": project.episode_number,
        "topic": project.topic,
        "status": project.status,
        "created_at": project.created_at.isoformat(),
    }
