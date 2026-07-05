from typing import Optional

from fastapi import Path, Query, Request
from pydantic import BaseModel

from app.controllers.v1.base import new_router
from app.db import session_scope
from app.db.models import VideoProject
from app.models.exception import HttpException
from app.models.schema import MaterialInfo
from app.services import storyboard
from app.utils import utils

router = new_router()


class ReplaceClipRequest(BaseModel):
    provider: str
    url: str
    duration: int = 5


@router.get("/projects/{project_id}/clips", summary="List the current render's clips in narrative order")
def get_clips(request: Request, project_id: int = Path(...)):
    with session_scope() as session:
        if session.get(VideoProject, project_id) is None:
            raise HttpException(task_id="", status_code=404, message=f"project {project_id} not found")
    clips = storyboard.list_clips(project_id)
    return utils.get_response(
        200,
        {
            "clips": [
                {
                    "index": c.index,
                    "search_term": c.search_term,
                    "provider": c.provider,
                    "source_url": c.source_url,
                    "local_path": c.local_path,
                }
                for c in clips
            ]
        },
    )


@router.get(
    "/projects/{project_id}/clips/{index}/candidates",
    summary="Search for a replacement stock clip for one storyboard entry",
)
def get_clip_candidates(
    request: Request,
    project_id: int = Path(...),
    index: int = Path(...),
    query: Optional[str] = Query(None),
    provider: str = Query("pexels"),
):
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        if project is None:
            raise HttpException(task_id="", status_code=404, message=f"project {project_id} not found")

    clips = {c.index: c for c in storyboard.list_clips(project_id)}
    clip = clips.get(index)
    if clip is None:
        raise HttpException(task_id="", status_code=404, message=f"project {project_id} has no clip at index {index}")

    search_term = query or clip.search_term
    if not search_term:
        raise HttpException(task_id="", status_code=400, message="no search query given and this clip has no original search term")

    candidates = storyboard.search_candidates(provider, search_term)
    return utils.get_response(
        200,
        {
            "candidates": [
                {"provider": c.provider, "url": c.url, "duration": c.duration} for c in candidates
            ]
        },
    )


@router.post("/projects/{project_id}/clips/{index}/replace", summary="Swap one clip and re-render just the assembly step")
def replace_clip(request: Request, body: ReplaceClipRequest, project_id: int = Path(...), index: int = Path(...)):
    candidate = MaterialInfo(provider=body.provider, url=body.url, duration=body.duration)
    try:
        new_video_path = storyboard.replace_clip_and_rerender(project_id, index, candidate)
    except storyboard.ClipNotFoundError as exc:
        raise HttpException(task_id="", status_code=404, message=str(exc))
    except storyboard.ProjectNotRenderedError as exc:
        raise HttpException(task_id="", status_code=409, message=str(exc))
    except ValueError as exc:
        raise HttpException(task_id="", status_code=404, message=str(exc))
    return utils.get_response(200, {"project_id": project_id, "video_path": new_video_path})
