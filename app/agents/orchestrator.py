import threading
from typing import List, Optional

from loguru import logger
from sqlmodel import select

from app.agents import base as agent_base
from app.agents.creative_director import CreativeDirector
from app.agents.producer import Producer
from app.agents.publisher import Publisher
from app.agents.quality_reviewer import QualityReviewer
from app.agents.schemas import CreativeBrief, QAReport
from app.agents.trend_scout import TrendScout
from app.config import config
from app.db import session_scope
from app.db.models import AgentEvent, ProjectStatus, VideoProject, utcnow
from app.models.schema import VideoAspect, VideoConcatMode, VideoParams
from app.services.ws_manager import broadcast_event, broadcast_status

# Statuses a project can be resumed from on startup after a crash. Any project
# still in one of these (and with a topic already picked) when the process
# starts was interrupted mid-pipeline; _run_pipeline restarts the stage rather
# than resuming a specific sub-step.
_RESUMABLE_STATUSES = {
    ProjectStatus.IDEA_READY.value,
    ProjectStatus.SCRIPTING.value,
    ProjectStatus.SCRIPT_READY.value,
    ProjectStatus.PRODUCING.value,
    ProjectStatus.RENDERED.value,
    ProjectStatus.QA_REVIEW.value,
}
_RECENT_TOPICS_LIMIT = 30


def _log_event(project_id: int, message: str, type_: str = "output") -> None:
    with session_scope() as session:
        session.add(AgentEvent(project_id=project_id, agent="orchestrator", type=type_, message=message))
        session.commit()
    broadcast_event(project_id, "orchestrator", type_, message)


def _set_status(project_id: int, status: ProjectStatus, **fields) -> None:
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        project.status = status.value
        for key, value in fields.items():
            setattr(project, key, value)
        session.add(project)
        session.commit()
    broadcast_status(project_id, status.value)


def _max_revisions() -> int:
    return int(config.agents.get("max_revisions", 2))


def _recent_performance_notes(niche: str, limit: int = 5) -> list[str]:
    # Feeds prior "what worked / what didn't" insights back into the next
    # Trend Scout run for this niche.
    if not niche:
        return []
    with session_scope() as session:
        projects = session.exec(
            select(VideoProject)
            .where(VideoProject.niche == niche)
            .where(VideoProject.analytics.is_not(None))
            .order_by(VideoProject.updated_at.desc())
            .limit(limit)
        ).all()
    notes = []
    for project in projects:
        for checkpoint in (project.analytics or {}).get("checkpoints", []):
            if checkpoint.get("note"):
                notes.append(checkpoint["note"])
    return notes


def _recent_topics() -> list[str]:
    # Trend Scout must not repropose a topic used in the last 30 projects.
    with session_scope() as session:
        rows = session.exec(
            select(VideoProject.topic)
            .where(VideoProject.topic.is_not(None))
            .order_by(VideoProject.created_at.desc())
            .limit(_RECENT_TOPICS_LIMIT)
        ).all()
    return [t for t in rows if t]


def start_manual_project(topic: str, niche: str = "") -> int:
    """
    Creates a project from a human-supplied topic (skips the Trend Scout) and
    runs it in a background thread to the current end of the pipeline. Returns
    immediately with the new project's id.
    """
    with session_scope() as session:
        project = VideoProject(status=ProjectStatus.SCRIPTING.value, niche=niche, topic=topic)
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    _log_event(project_id, f"Manual topic accepted: {topic!r}")
    thread = threading.Thread(target=_run_pipeline, args=(project_id, topic, niche), daemon=True)
    thread.start()
    return project_id


def start_auto_trend_project(niche: str, audience: str) -> int:
    """
    Creates a project with no human-supplied topic: the Trend Scout proposes
    ideas and the top-scoring one (excluding recently used topics) is picked
    automatically. Returns immediately with the new project's id.
    """
    with session_scope() as session:
        project = VideoProject(status=ProjectStatus.IDEA_PENDING.value, niche=niche)
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    thread = threading.Thread(target=_run_auto_trend_pipeline, args=(project_id, niche, audience), daemon=True)
    thread.start()
    return project_id


def _run_auto_trend_pipeline(project_id: int, niche: str, audience: str) -> None:
    try:
        if not agent_base.is_configured():
            raise agent_base.AgentNotConfiguredError(
                "agents.anthropic_api_key is not configured; cannot run the Trend Scout"
            )
        scout = TrendScout(project_id)
        report = scout.scout(
            niche=niche,
            audience=audience,
            recent_topics=_recent_topics(),
            performance_notes=_recent_performance_notes(niche),
        )
        with session_scope() as session:
            project = session.get(VideoProject, project_id)
            project.trend_report = report.model_dump()
            session.add(project)
            session.commit()

        best = max(report.ideas, key=lambda idea: idea.opportunity_score)
        _log_event(project_id, f"Trend Scout picked {best.title!r} (opportunity score {best.opportunity_score})")
        _set_status(project_id, ProjectStatus.IDEA_READY, topic=best.title)
        _run_pipeline(project_id, best.title, niche)
    except Exception as exc:  # noqa: BLE001 - surface any pipeline failure on the project
        logger.exception(f"project {project_id} trend scouting failed")
        _set_status(project_id, ProjectStatus.FAILED, failure_reason=str(exc))
        _log_event(project_id, f"Trend scouting failed: {exc}", type_="error")


def _write_brief(project_id: int, topic: str, niche: str, revision_notes: Optional[str]) -> CreativeBrief:
    director = CreativeDirector(project_id)
    brief = director.write(topic=topic, niche=niche, revision_notes=revision_notes)
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        project.brief = brief.model_dump()
        session.add(project)
        session.commit()
    return brief


def _video_params_from_brief(topic: str, brief: CreativeBrief) -> VideoParams:
    return VideoParams(
        video_subject=topic,
        video_script=brief.script,
        video_terms=brief.search_terms,
        match_materials_to_script=True,
        video_concat_mode=VideoConcatMode.sequential.value,
        video_aspect=VideoAspect.portrait.value,
        voice_name=brief.voice_recommendation or config.ui.get("voice_name", ""),
        bgm_type="random",
        bgm_file=brief.bgm_file or "",
    )


def _append_qa_report(project_id: int, report: QAReport) -> None:
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        reports = list(project.qa_reports or [])
        reports.append(report.model_dump())
        project.qa_reports = reports
        session.add(project)
        session.commit()


def _prepare_publish_package(project_id: int, brief: CreativeBrief, video_path: str, niche: str) -> None:
    publisher = Publisher(project_id)
    hook_text = brief.metadata_draft.hook_variants[0] if brief.metadata_draft.hook_variants else brief.metadata_draft.working_title
    package = publisher.prepare(script=brief.script, niche=niche, hook_text=hook_text, video_path=video_path)
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        project.publish_package = package
        session.add(project)
        session.commit()


def _run_pipeline(project_id: int, topic: str, niche: str = "", revision_notes: Optional[str] = None) -> None:
    try:
        if not agent_base.is_configured():
            raise agent_base.AgentNotConfiguredError(
                "agents.anthropic_api_key is not configured; cannot run the Creative Director"
            )

        _set_status(project_id, ProjectStatus.SCRIPTING, topic=topic)
        brief = _write_brief(project_id, topic, niche, revision_notes)
        _set_status(project_id, ProjectStatus.SCRIPT_READY)
        _log_event(project_id, "Creative Director produced a script and brief")

        _set_status(project_id, ProjectStatus.PRODUCING)
        params = _video_params_from_brief(topic, brief)
        producer = Producer(project_id)
        final_state = producer.run(params)
        _set_status(project_id, ProjectStatus.RENDERED)

        _set_status(project_id, ProjectStatus.QA_REVIEW)
        with session_scope() as session:
            project = session.get(VideoProject, project_id)
            video_path = project.video_path
        reviewer = QualityReviewer(project_id)
        qa_report = reviewer.review(
            video_path=video_path, script=brief.script, subtitle_path=final_state.get("subtitle_path")
        )
        _append_qa_report(project_id, qa_report)

        if qa_report.overall == "pass":
            _set_status(project_id, ProjectStatus.QA_PASSED)
            _prepare_publish_package(project_id, brief, video_path, niche)
            _set_status(project_id, ProjectStatus.AWAITING_HUMAN_APPROVAL)
            _log_event(project_id, "QA passed, publish package prepared, awaiting human approval")
            return

        if qa_report.overall == "fail":
            _set_status(
                project_id,
                ProjectStatus.FAILED,
                failure_reason=f"QA failed: {qa_report.revision_notes or 'unusable output'}",
            )
            _log_event(project_id, "QA marked the video unusable; escalated", type_="error")
            return

        # overall == "revise" - loop back through the Creative Director, capped
        # at max_revisions automatic loops before escalating to a human.
        with session_scope() as session:
            project = session.get(VideoProject, project_id)
            current_revision_count = project.revision_count

        if current_revision_count >= _max_revisions():
            _set_status(
                project_id,
                ProjectStatus.FAILED,
                failure_reason=(
                    f"QA requested a revision but the limit ({_max_revisions()}) was reached; "
                    "escalated for human review with the QA report attached."
                ),
            )
            _log_event(project_id, "Revision limit reached after QA feedback, escalating", type_="error")
            return

        _set_status(project_id, ProjectStatus.SCRIPTING, revision_count=current_revision_count + 1)
        _log_event(
            project_id,
            f"QA requested a revision ({current_revision_count + 1}/{_max_revisions()}): {qa_report.revision_notes}",
        )
        _run_pipeline(project_id, topic, niche, qa_report.revision_notes)
    except Exception as exc:  # noqa: BLE001 - surface any pipeline failure on the project
        logger.exception(f"project {project_id} failed")
        _set_status(project_id, ProjectStatus.FAILED, failure_reason=str(exc))
        _log_event(project_id, f"Pipeline failed: {exc}", type_="error")


def retry_with_revision(project_id: int, revision_notes: str) -> None:
    """
    Reject-with-notes: reruns the Creative Director with feedback and
    re-produces the video. Enforces max_revisions from config.toml - beyond
    that, the project is left FAILED with a note so a human can take over,
    per the hard cap on automatic revision loops.
    """
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        if project is None:
            raise ValueError(f"project {project_id} not found")
        topic = project.topic
        niche = project.niche or ""
        revision_count = project.revision_count

    if revision_count >= _max_revisions():
        _set_status(
            project_id,
            ProjectStatus.FAILED,
            failure_reason=f"revision limit ({_max_revisions()}) reached; escalated for human review",
        )
        _log_event(project_id, "Revision limit reached, escalating to human", type_="error")
        return

    _set_status(project_id, ProjectStatus.SCRIPTING, revision_count=revision_count + 1)
    _log_event(project_id, f"Revision {revision_count + 1}/{_max_revisions()} requested: {revision_notes}")
    thread = threading.Thread(
        target=_run_pipeline, args=(project_id, topic, niche, revision_notes), daemon=True
    )
    thread.start()


def approve_and_publish(project_id: int, platforms: List[str], thumbnail_path: Optional[str] = None) -> None:
    """
    The mandatory human approval gate. Publishing is only ever triggered from
    here, and only when the project is actually awaiting approval - this is
    the one and only path that calls Publisher.publish().
    """
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        if project is None:
            raise ValueError(f"project {project_id} not found")
        if project.status != ProjectStatus.AWAITING_HUMAN_APPROVAL.value:
            raise PermissionError(
                f"project {project_id} is not awaiting approval (status={project.status}); refusing to publish"
            )
        video_path = project.video_path
        package = project.publish_package

    if not video_path or not package:
        raise RuntimeError(f"project {project_id} has no rendered video or publish package to publish")

    _set_status(project_id, ProjectStatus.APPROVED)
    _log_event(project_id, f"Approved for platforms: {', '.join(platforms)}")
    thread = threading.Thread(
        target=_run_publish, args=(project_id, video_path, package, platforms, thumbnail_path), daemon=True
    )
    thread.start()


def _run_publish(
    project_id: int, video_path: str, package: dict, platforms: List[str], thumbnail_path: Optional[str]
) -> None:
    try:
        _set_status(project_id, ProjectStatus.PUBLISHING)
        publisher = Publisher(project_id)
        results = publisher.publish(
            video_path=video_path, package=package, platforms=platforms, thumbnail_path=thumbnail_path
        )
        with session_scope() as session:
            project = session.get(VideoProject, project_id)
            project.published_posts = results
            session.add(project)
            session.commit()

        if all(r.get("success") for r in results):
            _set_status(project_id, ProjectStatus.PUBLISHED, published_at=utcnow())
            _log_event(project_id, "Published successfully")
            # Tracking begins immediately; the Performance Analyst scheduler
            # job picks up the 24h/72h checkpoints from here.
            _set_status(project_id, ProjectStatus.TRACKING)
        else:
            _set_status(project_id, ProjectStatus.FAILED, failure_reason=f"publish failed: {results}")
            _log_event(project_id, f"Publish failed: {results}", type_="error")
    except Exception as exc:  # noqa: BLE001 - surface any publish failure on the project
        logger.exception(f"project {project_id} publish failed")
        _set_status(project_id, ProjectStatus.FAILED, failure_reason=str(exc))
        _log_event(project_id, f"Publish failed: {exc}", type_="error")


def retry_failed_project(project_id: int) -> None:
    """
    Plain retry with no revision notes, for infra-type failures (e.g. a
    transient render error) rather than content feedback. Does not consume a
    revision slot.
    """
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        if project is None:
            raise ValueError(f"project {project_id} not found")
        if project.status != ProjectStatus.FAILED.value:
            raise PermissionError(f"project {project_id} is not FAILED (status={project.status})")
        topic = project.topic
        niche = project.niche or ""

    _set_status(project_id, ProjectStatus.SCRIPTING, failure_reason=None)
    _log_event(project_id, "Retrying after failure")
    thread = threading.Thread(target=_run_pipeline, args=(project_id, topic or "", niche), daemon=True)
    thread.start()


def resume_incomplete_projects() -> None:
    """
    Called at startup. Any project left in an in-flight status when the
    process last stopped was interrupted mid-pipeline (crash, restart) —
    re-run it from the top of its current stage.
    """
    with session_scope() as session:
        stuck = session.exec(select(VideoProject).where(VideoProject.status.in_(_RESUMABLE_STATUSES))).all()
        stuck_ids_topics = [(p.id, p.topic, p.niche) for p in stuck]

    for project_id, topic, niche in stuck_ids_topics:
        logger.info(f"resuming interrupted project {project_id}")
        _log_event(project_id, "Resuming after restart")
        thread = threading.Thread(target=_run_pipeline, args=(project_id, topic or "", niche or ""), daemon=True)
        thread.start()


_PERFORMANCE_CHECKPOINTS_HOURS = (24, 72)


def run_performance_checks() -> None:
    """
    Called periodically by the scheduler. For every TRACKING project, checks
    whether a 24h or 72h post-publish checkpoint is due and, if so, pulls
    view/like/comment counts (and a short insight) via the Performance
    Analyst. Archives a project once its final checkpoint is recorded, or
    immediately if analytics can't be checked at all (no YouTube key, or no
    YouTube post found) so it isn't re-evaluated on every tick.
    """
    from app.agents.performance_analyst import PerformanceAnalyst
    from app.services import youtube_analytics

    now = utcnow()
    with session_scope() as session:
        tracking = session.exec(select(VideoProject).where(VideoProject.status == ProjectStatus.TRACKING.value)).all()
        snapshot = [
            (p.id, p.published_at, p.published_posts, p.brief, p.niche, p.analytics) for p in tracking
        ]

    for project_id, published_at, published_posts, brief, niche, analytics in snapshot:
        if published_at is None:
            continue

        if not youtube_analytics.is_configured():
            _set_status(project_id, ProjectStatus.ARCHIVED)
            _log_event(project_id, "Analytics not configured (no YouTube Data API key); archiving without tracking")
            continue

        video_id = youtube_analytics.extract_youtube_video_id(published_posts)
        if not video_id:
            _set_status(project_id, ProjectStatus.ARCHIVED)
            _log_event(project_id, "No YouTube post found among published platforms; archiving without tracking")
            continue

        elapsed_hours = (now - published_at).total_seconds() / 3600
        analytics = analytics or {}
        done_checkpoints = {c["checkpoint_hours"] for c in analytics.get("checkpoints", [])}

        for checkpoint_hours in _PERFORMANCE_CHECKPOINTS_HOURS:
            if checkpoint_hours in done_checkpoints or elapsed_hours < checkpoint_hours:
                continue
            analyst = PerformanceAnalyst(project_id)
            script = (brief or {}).get("script", "")
            result = analyst.check(
                video_id, checkpoint_hours, script, niche or "", analytics.get("checkpoints")
            )
            if result is None:
                continue
            with session_scope() as session:
                project = session.get(VideoProject, project_id)
                current = list((project.analytics or {}).get("checkpoints", []))
                current.append(result)
                project.analytics = {"checkpoints": current}
                session.add(project)
                session.commit()
            done_checkpoints.add(checkpoint_hours)

        if max(_PERFORMANCE_CHECKPOINTS_HOURS) in done_checkpoints:
            _set_status(project_id, ProjectStatus.ARCHIVED)
            _log_event(project_id, "Final performance checkpoint recorded; archiving")
