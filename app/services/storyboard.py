"""
Clip-index bridge (docs/DECISIONS_V3.md §4): Final Review's storyboard strip
and per-scene media replacement, scoped to what the current flat
search-terms renderer actually produces - NOT the full vision-scored Visual
Director / ScenePlan model from docs/DESIGN_V2.md, which was never built
(see docs/DECISIONS_V3.md §0/§4 for why that's a deliberate scope line, not
an oversight).

Each ProjectClip row is one stock clip already used in the current render,
in narrative order. Swapping one clip triggers a *targeted* re-render: only
the combine/encode step re-runs, reusing the same voiceover/subtitles - the
script, TTS, and subtitle generation are untouched.
"""

import os
from typing import List, Optional

from loguru import logger
from sqlmodel import delete, select

from app.db import session_scope
from app.db.models import ProjectClip, VideoProject
from app.models.schema import MaterialInfo, VideoAspect, VideoParams
from app.services import material as material_service
from app.services import project_storage
from app.utils import utils


def record_clips(project_id: int, clip_metadata: List[dict]) -> None:
    """
    Replaces this project's clip index with the current render's clips.
    Called after every render (initial or a full re-render), so it always
    reflects final-video.mp4 - stale entries from a prior render must not
    linger and be mistaken for the current storyboard.
    """
    with session_scope() as session:
        session.exec(delete(ProjectClip).where(ProjectClip.project_id == project_id))
        for index, clip in enumerate(clip_metadata or []):
            session.add(
                ProjectClip(
                    project_id=project_id,
                    index=index,
                    search_term=clip.get("search_term", ""),
                    provider=clip.get("provider", ""),
                    source_url=clip.get("url", ""),
                    local_path=clip.get("local_path", ""),
                )
            )
        session.commit()


def list_clips(project_id: int) -> List[ProjectClip]:
    with session_scope() as session:
        return session.exec(
            select(ProjectClip).where(ProjectClip.project_id == project_id).order_by(ProjectClip.index)
        ).all()


_SEARCH_FUNCTION_NAMES = {
    "pexels": "search_videos_pexels",
    "pixabay": "search_videos_pixabay",
    "coverr": "search_videos_coverr",
}


def search_candidates(provider: str, query: str, video_aspect: VideoAspect = VideoAspect.portrait) -> List[MaterialInfo]:
    # Looked up by name (not a module-level function reference) so tests can
    # patch material_service.search_videos_* the same way they patch any
    # other material.py call site.
    function_name = _SEARCH_FUNCTION_NAMES.get(provider, _SEARCH_FUNCTION_NAMES["pexels"])
    search_fn = getattr(material_service, function_name)
    return search_fn(search_term=query, minimum_duration=1, video_aspect=video_aspect)


class ClipNotFoundError(ValueError):
    pass


class ProjectNotRenderedError(ValueError):
    pass


def replace_clip_and_rerender(project_id: int, clip_index: int, candidate: MaterialInfo) -> str:
    """
    Downloads `candidate` in place of the clip at clip_index, then re-runs
    only the assembly/encode step (task.py's generate_final_videos) with the
    updated ordered clip list - script, TTS audio, and subtitles are reused
    unchanged from the original render. Returns the new final video path.
    """
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        if project is None:
            raise ValueError(f"project {project_id} not found")
        if not project.task_id or not project.video_params:
            raise ProjectNotRenderedError(f"project {project_id} has not been rendered yet")
        task_id = project.task_id
        video_params_dict = project.video_params

        clip = session.exec(
            select(ProjectClip)
            .where(ProjectClip.project_id == project_id)
            .where(ProjectClip.index == clip_index)
        ).first()
        if clip is None:
            raise ClipNotFoundError(f"project {project_id} has no clip at index {clip_index}")

    task_dir = utils.task_dir(task_id)
    saved_path = material_service.save_video(video_url=candidate.url, save_dir=task_dir)
    if not saved_path:
        raise RuntimeError(f"failed to download replacement clip: {candidate.url}")

    with session_scope() as session:
        clip = session.get(ProjectClip, clip.id)
        clip.provider = candidate.provider
        clip.source_url = candidate.url
        clip.local_path = saved_path
        session.add(clip)
        session.commit()

    ordered_clips = list_clips(project_id)
    ordered_paths = [c.local_path for c in ordered_clips if c.local_path]

    params = VideoParams.model_validate(video_params_dict)
    audio_file = os.path.join(task_dir, "audio.mp3")
    subtitle_path = os.path.join(task_dir, "subtitle.srt")
    if not os.path.isfile(subtitle_path):
        subtitle_path = ""

    from app.services import task as task_service

    final_video_paths, _combined_paths = task_service.generate_final_videos(
        task_id, params, ordered_paths, audio_file, subtitle_path
    )
    if not final_video_paths:
        raise RuntimeError(f"re-render produced no output video for project {project_id}")
    new_video_path = final_video_paths[0]

    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        project.video_path = new_video_path
        session.add(project)
        session.commit()

    logger.info(f"project {project_id}: clip {clip_index} replaced, targeted re-render complete")
    project_storage.materialize_project(project_id)
    return new_video_path
