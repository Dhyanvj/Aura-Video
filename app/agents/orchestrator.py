import threading

from loguru import logger
from sqlmodel import select

from app.agents.producer import Producer
from app.config import config
from app.db import session_scope
from app.db.models import AgentEvent, ProjectStatus, VideoProject
from app.models.schema import VideoAspect, VideoParams

# Statuses a project can be resumed from on startup after a crash. Any project
# still in one of these when the process starts was interrupted mid-pipeline.
_RESUMABLE_STATUSES = {ProjectStatus.SCRIPTING.value, ProjectStatus.PRODUCING.value}


def _log_event(project_id: int, message: str, type_: str = "output") -> None:
    with session_scope() as session:
        session.add(AgentEvent(project_id=project_id, agent="orchestrator", type=type_, message=message))
        session.commit()


def _set_status(project_id: int, status: ProjectStatus, **fields) -> None:
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        project.status = status.value
        for key, value in fields.items():
            setattr(project, key, value)
        session.add(project)
        session.commit()


def _build_manual_video_params(topic: str) -> VideoParams:
    # M2: no Creative Director yet, so the script/terms are left blank and the
    # existing legacy pipeline (app/services/llm.generate_script/generate_terms)
    # fills them in, same as a direct API call to POST /videos would.
    return VideoParams(
        video_subject=topic,
        voice_name=config.ui.get("voice_name", ""),
        video_aspect=VideoAspect.portrait.value,
    )


def start_manual_project(topic: str, niche: str = "") -> int:
    """
    Creates a project from a human-supplied topic (skips the Trend Scout) and
    runs it in a background thread to the current end of the pipeline. Returns
    immediately with the new project's id.
    """
    with session_scope() as session:
        project = VideoProject(status=ProjectStatus.PRODUCING.value, niche=niche, topic=topic)
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    _log_event(project_id, f"Manual topic accepted: {topic!r}")
    thread = threading.Thread(target=_run_pipeline, args=(project_id, topic), daemon=True)
    thread.start()
    return project_id


def _run_pipeline(project_id: int, topic: str) -> None:
    try:
        params = _build_manual_video_params(topic)
        producer = Producer(project_id)
        producer.run(params)
        # QA (M4) and Publisher (M5) are not wired in yet, so a rendered video
        # goes straight to the human-approval gate.
        _set_status(project_id, ProjectStatus.AWAITING_HUMAN_APPROVAL)
        _log_event(project_id, "Render complete, awaiting human approval")
    except Exception as exc:  # noqa: BLE001 - surface any pipeline failure on the project
        logger.exception(f"project {project_id} failed")
        _set_status(project_id, ProjectStatus.FAILED, failure_reason=str(exc))
        _log_event(project_id, f"Pipeline failed: {exc}", type_="error")


def resume_incomplete_projects() -> None:
    """
    Called at startup. Any project left in an in-flight status when the
    process last stopped was interrupted mid-pipeline (crash, restart) —
    re-run it from the top of its current stage.
    """
    with session_scope() as session:
        stuck = session.exec(select(VideoProject).where(VideoProject.status.in_(_RESUMABLE_STATUSES))).all()
        stuck_ids_topics = [(p.id, p.topic) for p in stuck]

    for project_id, topic in stuck_ids_topics:
        logger.info(f"resuming interrupted project {project_id}")
        _log_event(project_id, "Resuming after restart")
        thread = threading.Thread(target=_run_pipeline, args=(project_id, topic or ""), daemon=True)
        thread.start()
