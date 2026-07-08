import threading
from typing import List, Optional

from loguru import logger
from sqlmodel import select

from app.agents import base as agent_base
from app.agents.creative_director import CreativeDirector
from app.agents.producer import Producer
from app.agents.publisher import Publisher
from app.agents.quality_reviewer import QualityReviewer
from app.agents.researcher import Researcher
from app.agents.schemas import CreativeBrief, QAReport, ResearchDossier, SourceCitation
from app.agents.trend_scout import TrendScout
from app.config import config
from app.db import session_scope
from app.db.models import AgentEvent, ContentTypeTemplate, ProjectStatus, Series, VideoProject, utcnow
from app.models.schema import VideoAspect, VideoConcatMode, VideoParams
from app.services import cancellation, originality, playbook, project_storage, storyboard
from app.services.cancellation import PipelineCancelled
from app.services.ws_manager import broadcast_event, broadcast_status

# Content types where a news story older than its freshness window, or one
# that couldn't be corroborated from independent sources, must never reach a
# script - "never a vague roundup" per spec. Motivational/fun_facts also
# require research, but a thin research pass there should make the Creative
# Director more conservative (it's already instructed to fall back to a life
# lesson over a risky quote), not hard-fail the project outright.
_NEWS_CONTENT_TYPES = {"ai_news", "world_news"}

# Statuses a project can be resumed from on startup after a crash. Any project
# still in one of these (and with a topic already picked) when the process
# starts was interrupted mid-pipeline; _run_pipeline restarts the stage rather
# than resuming a specific sub-step.
_RESUMABLE_STATUSES = {
    ProjectStatus.IDEA_READY.value,
    ProjectStatus.RESEARCHING.value,
    ProjectStatus.RESEARCH_READY.value,
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


def _mark_cancelled(project_id: int) -> None:
    _set_status(project_id, ProjectStatus.CANCELLED)
    _log_event(project_id, "Cancelled by user request")


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


def _recent_topics(content_type_id: Optional[str] = None, series_id: Optional[int] = None) -> list[str]:
    """
    Dedupe scope depends on what's given: a series must never repeat one of
    its own episode topics, and (absent a series) a content type must not
    repeat a topic from another project of the same type - but there's no
    reason a "fun_facts" episode's dedupe window should be polluted by an
    unrelated "motivational" topic, or vice versa. Called with neither for
    legacy/global callers, which keeps the original cross-everything window.
    """
    with session_scope() as session:
        query = select(VideoProject.topic).where(VideoProject.topic.is_not(None))
        if series_id is not None:
            query = query.where(VideoProject.series_id == series_id)
        elif content_type_id is not None:
            query = query.where(VideoProject.content_type_id == content_type_id)
        rows = session.exec(query.order_by(VideoProject.created_at.desc()).limit(_RECENT_TOPICS_LIMIT)).all()
    return [t for t in rows if t]


_RECENT_HOOK_PATTERNS_LIMIT = 10


def _recent_hook_patterns(content_type_id: Optional[str]) -> list[str]:
    """Last 10 hook_pattern values used for this content type, most recent first (docs/DECISIONS_V3.md §2)."""
    if not content_type_id:
        return []
    with session_scope() as session:
        query = (
            select(VideoProject.hook_pattern)
            .where(VideoProject.content_type_id == content_type_id)
            .where(VideoProject.hook_pattern.is_not(None))
            .order_by(VideoProject.created_at.desc())
            .limit(_RECENT_HOOK_PATTERNS_LIMIT)
        )
        rows = session.exec(query).all()
    return [p for p in rows if p]


_RECENT_SCRIPTS_LIMIT = 5


def _recent_scripts(content_type_id: Optional[str], exclude_project_id: int) -> list[str]:
    """Last 5 scripts of this content type (excluding this project itself), for the script-repetition check."""
    if not content_type_id:
        return []
    with session_scope() as session:
        query = (
            select(VideoProject)
            .where(VideoProject.content_type_id == content_type_id)
            .where(VideoProject.id != exclude_project_id)
            .where(VideoProject.brief.is_not(None))
            .order_by(VideoProject.created_at.desc())
            .limit(_RECENT_SCRIPTS_LIMIT)
        )
        projects = session.exec(query).all()
    return [p.brief["script"] for p in projects if p.brief and p.brief.get("script")]


def _get_content_type_info(content_type_id: Optional[str]) -> Optional[dict]:
    if not content_type_id:
        return None
    with session_scope() as session:
        template = session.get(ContentTypeTemplate, content_type_id)
        if template is None:
            return None
        return {
            "research_required": template.research_required,
            "freshness_window_hours": template.freshness_window_hours,
            "ai_gen_allowed": bool((template.visual_strategy or {}).get("ai_gen_allowed")),
        }


def _run_research(
    project_id: int,
    content_type_id: str,
    topic_hint: str,
    niche: str,
    series_id: Optional[int],
    freshness_window_hours: Optional[int],
) -> ResearchDossier:
    researcher = Researcher(project_id)
    dossier = researcher.research(
        content_type_id=content_type_id,
        topic_hint=topic_hint,
        niche=niche,
        recent_topics=_recent_topics(content_type_id=content_type_id, series_id=series_id),
        performance_notes=_recent_performance_notes(niche),
        freshness_window_hours=freshness_window_hours,
    )
    _log_event(
        project_id,
        f"Researcher: {'reduced verification' if dossier.reduced_verification else f'{len(dossier.sources)} source(s)'} "
        f"for {dossier.topic!r}",
        type_="error" if dossier.reduced_verification else "output",
    )
    return dossier


def _store_research_evidence(project_id: int, dossier: ResearchDossier) -> None:
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        project.research_evidence = dossier.model_dump()
        session.add(project)
        session.commit()


def _dossier_from_trend_idea(idea) -> ResearchDossier:
    """
    Trending Now doesn't need a Researcher pass - Trend Scout's own YouTube/
    pytrends signals already ARE the evidence for "where this is trending" -
    so this just reshapes what Trend Scout already found into the same
    dossier shape everything else stores as research_evidence, for a
    consistent Project Detail UI.
    """
    return ResearchDossier(
        topic=idea.title,
        why_now=idea.why_trending,
        sources=[SourceCitation(url=e, title=e) for e in idea.evidence if e.startswith("http")],
        suggested_angle=idea.suggested_format,
    )


def create_series(content_type_id: str, title: str, style_guide: Optional[dict] = None) -> int:
    """
    Starts a new Series Bible with no locked voice yet - the founding
    episode's Creative Director recommendation locks it in (see
    _resolve_voice_name).
    """
    with session_scope() as session:
        series = Series(content_type_id=content_type_id, title=title, style_guide=style_guide or {})
        session.add(series)
        session.commit()
        session.refresh(series)
        return series.id


def next_episode_number(series_id: int) -> int:
    """Reserves and returns the next episode number for a series, bumping its counter."""
    with session_scope() as session:
        series = session.get(Series, series_id)
        if series is None:
            raise ValueError(f"series {series_id} not found")
        series.episode_counter += 1
        series.updated_at = utcnow()
        session.add(series)
        session.commit()
        return series.episode_counter


def start_manual_project(
    topic: str,
    niche: str = "",
    content_type_id: Optional[str] = None,
    quality_preset: Optional[str] = None,
    series_id: Optional[int] = None,
    episode_number: Optional[int] = None,
) -> int:
    """
    Creates a project from a human-supplied topic (skips the Trend Scout) and
    runs it in a background thread to the current end of the pipeline. Returns
    immediately with the new project's id.
    """
    with session_scope() as session:
        project = VideoProject(
            status=ProjectStatus.SCRIPTING.value,
            niche=niche,
            topic=topic,
            content_type_id=content_type_id,
            quality_preset=quality_preset,
            series_id=series_id,
            episode_number=episode_number,
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    _log_event(project_id, f"Manual topic accepted: {topic!r}")
    thread = threading.Thread(target=_run_pipeline, args=(project_id, topic, niche), daemon=True)
    thread.start()
    return project_id


def start_auto_trend_project(
    niche: str,
    audience: str,
    content_type_id: Optional[str] = None,
    quality_preset: Optional[str] = None,
    series_id: Optional[int] = None,
    episode_number: Optional[int] = None,
) -> int:
    """
    Creates a project with no human-supplied topic: the Trend Scout proposes
    ideas and the top-scoring one (excluding recently used topics) is picked
    automatically. Returns immediately with the new project's id.
    """
    with session_scope() as session:
        project = VideoProject(
            status=ProjectStatus.IDEA_PENDING.value,
            niche=niche,
            content_type_id=content_type_id,
            quality_preset=quality_preset,
            series_id=series_id,
            episode_number=episode_number,
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    thread = threading.Thread(target=_run_auto_trend_pipeline, args=(project_id, niche, audience), daemon=True)
    thread.start()
    return project_id


def _run_auto_trend_pipeline(project_id: int, niche: str, audience: str) -> None:
    try:
        cancellation.raise_if_cancelled(project_id)
        if not agent_base.is_configured():
            raise agent_base.AgentNotConfiguredError(
                "agents.anthropic_api_key is not configured; cannot run the Trend Scout"
            )

        with session_scope() as session:
            project = session.get(VideoProject, project_id)
            content_type_id = project.content_type_id
            series_id = project.series_id

        content_type_info = _get_content_type_info(content_type_id)

        if content_type_info and content_type_info["research_required"]:
            # Trend Scout's YouTube/pytrends signals answer "what's trending" -
            # they can't tell you a verified quote or a fresh, corroborated
            # news story. For these content types the Researcher IS the topic
            # source in autopilot, not just a verification pass afterward.
            _set_status(project_id, ProjectStatus.RESEARCHING)
            dossier = _run_research(
                project_id, content_type_id, "", niche, series_id, content_type_info["freshness_window_hours"]
            )
            _store_research_evidence(project_id, dossier)

            if dossier.reduced_verification:
                # The spec's autopilot evidence gate: an idea without evidence
                # can't be auto-picked. Unlike the Trend-Scout path below,
                # there's no ranked ideas list here to leave for manual
                # selection via the Trends page - the Researcher only ever
                # proposes one candidate - so this fails the project with an
                # actionable reason (retry, or start a manual-topic project
                # for this content type) rather than a silent auto-continue.
                _set_status(
                    project_id,
                    ProjectStatus.FAILED,
                    failure_reason=(
                        "Autopilot could not verify a topic from independent sources for this content "
                        "type; retry, or start a project with a manually-chosen topic instead of "
                        "auto-picking without evidence."
                    ),
                )
                _log_event(
                    project_id,
                    "Research produced no verifiable topic; autopilot refuses to auto-pick without evidence",
                    type_="error",
                )
                return

            _set_status(project_id, ProjectStatus.RESEARCH_READY, topic=dossier.topic)
            _log_event(project_id, f"Researcher proposed the topic {dossier.topic!r} with verified evidence")
            _run_pipeline(project_id, dossier.topic, niche, dossier=dossier)
            return

        scout = TrendScout(project_id)
        report = scout.scout(
            niche=niche,
            audience=audience,
            recent_topics=_recent_topics(content_type_id=content_type_id, series_id=series_id),
            performance_notes=_recent_performance_notes(niche),
        )
        with session_scope() as session:
            project = session.get(VideoProject, project_id)
            project.trend_report = report.model_dump()
            session.add(project)
            session.commit()

        # Autopilot evidence gate: an idea with no supporting evidence cannot
        # be auto-picked, even if it scores highest - only consider ideas
        # Trend Scout actually backed with something (a link, a stat, a
        # signal), and leave the project idle for manual selection via the
        # Trends page if none qualify.
        eligible = [idea for idea in report.ideas if idea.evidence]
        if not eligible:
            _log_event(
                project_id,
                "No trend idea had supporting evidence; autopilot refuses to auto-pick without "
                "evidence - left idle for manual selection from the Trends page",
                type_="error",
            )
            _set_status(project_id, ProjectStatus.IDEA_PENDING)
            return

        # Originality gate (docs/DECISIONS_V3.md §2), applied while picking
        # from Trend Scout's own ranked ideas rather than as a separate
        # regenerate-and-recall-the-agent step: walk the list highest-scoring
        # first and skip any idea that collides with prior coverage - this is
        # "make Trend Scout generate a different concept" at zero extra LLM
        # cost, since it already proposed several. _run_pipeline still runs
        # the canonical evaluate_topic/commit_topic pass on whichever idea is
        # finally chosen.
        best = None
        for idea in sorted(eligible, key=lambda idea: idea.opportunity_score, reverse=True):
            check = originality.check_topic_originality(content_type_id, series_id, idea.title, idea.why_trending)
            if not check.rejected:
                best = idea
                break
            _log_event(
                project_id,
                f"Skipped trend idea {idea.title!r}: {check.reason}",
                type_="error",
            )

        if best is None:
            _set_status(
                project_id,
                ProjectStatus.FAILED,
                failure_reason="Every eligible trend idea collided with prior coverage; no original concept available.",
            )
            _log_event(project_id, "All eligible trend ideas rejected by the originality check", type_="error")
            return

        _log_event(project_id, f"Trend Scout picked {best.title!r} (opportunity score {best.opportunity_score})")

        dossier = None
        if content_type_id == "trending_now":
            dossier = _dossier_from_trend_idea(best)
            _store_research_evidence(project_id, dossier)

        cancellation.raise_if_cancelled(project_id)
        _set_status(project_id, ProjectStatus.IDEA_READY, topic=best.title)
        _run_pipeline(project_id, best.title, niche, dossier=dossier)
    except PipelineCancelled:
        _mark_cancelled(project_id)
    except Exception as exc:  # noqa: BLE001 - surface any pipeline failure on the project
        logger.exception(f"project {project_id} trend scouting failed")
        _set_status(project_id, ProjectStatus.FAILED, failure_reason=str(exc))
        _log_event(project_id, f"Trend scouting failed: {exc}", type_="error")


_ROLLING_SUMMARY_MAX_LINES = 5


def _append_series_summary(series_id: int, episode_number: Optional[int], topic: str) -> None:
    with session_scope() as session:
        series = session.get(Series, series_id)
        if series is None:
            return
        label = f"Episode {episode_number}" if episode_number else "An episode"
        lines = [line for line in series.rolling_summary.split("\n") if line]
        lines.append(f"{label}: {topic}")
        series.rolling_summary = "\n".join(lines[-_ROLLING_SUMMARY_MAX_LINES:])
        series.updated_at = utcnow()
        session.add(series)
        session.commit()


def _write_brief(
    project_id: int,
    topic: str,
    niche: str,
    revision_notes: Optional[str],
    dossier: Optional[ResearchDossier] = None,
) -> CreativeBrief:
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        content_type_id = project.content_type_id if project else None

    director = CreativeDirector(project_id)
    brief = director.write(
        topic=topic,
        niche=niche,
        revision_notes=revision_notes,
        content_type_id=content_type_id,
        research_dossier=dossier,
        recent_hook_patterns=_recent_hook_patterns(content_type_id),
        playbook_bullets=playbook.get_active_bullets("creative_director", content_type_id),
    )
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        project.brief = brief.model_dump()
        project.hook_pattern = brief.hook_pattern
        project.opening_line = brief.opening_line
        session.add(project)
        session.commit()
        series_id = project.series_id
        episode_number = project.episode_number

    if series_id:
        _append_series_summary(series_id, episode_number, topic)
    return brief


_FALLBACK_VOICE = "en-US-AndrewNeural-Male"


def _resolve_voice_name(project_id: int, brief: CreativeBrief) -> str:
    # Defense in depth: even with the Creative Director now given a real voice
    # list to pick from, validate before this reaches the render pipeline -
    # a hallucinated voice name (e.g. a text description instead of a real
    # TTS voice ID) would otherwise fail the render almost immediately.
    from app.services import voice as voice_service

    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        series = session.get(Series, project.series_id) if project and project.series_id else None

    candidate = (brief.voice_recommendation or "").strip()
    valid_voices = set(voice_service.get_all_azure_voices())

    if series is not None and series.voice_id:
        # Series continuity is a hard constraint, not a suggestion: voice
        # drift between episodes is a continuity failure, so this overrides
        # the Creative Director's recommendation outright rather than only
        # falling back when the value happens to be invalid.
        if candidate != series.voice_id:
            _log_event(
                project_id,
                f"Series voice enforced: using {series.voice_id!r} instead of the "
                f"recommended {candidate!r} to keep episode-to-episode continuity",
            )
        return series.voice_id

    resolved = candidate if candidate in valid_voices else (config.ui.get("voice_name", "") or _FALLBACK_VOICE)
    if candidate not in valid_voices:
        _log_event(
            project_id,
            f"Creative Director recommended an invalid voice ({candidate!r}); falling back to {resolved!r}",
            type_="error",
        )

    if series is not None and not series.voice_id:
        # Founding episode of a new series: lock this voice in for continuity.
        with session_scope() as session:
            series_row = session.get(Series, series.id)
            series_row.voice_id = resolved
            series_row.updated_at = utcnow()
            session.add(series_row)
            session.commit()
        _log_event(project_id, f"Locked series voice to {resolved!r} for future episodes")

    return resolved


def _ai_image_fallback_allowed(content_type_id: Optional[str]) -> bool:
    info = _get_content_type_info(content_type_id)
    return bool(info and info.get("ai_gen_allowed"))


def _video_params_from_brief(project_id: int, topic: str, brief: CreativeBrief) -> VideoParams:
    quote = brief.quote_or_lesson
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        content_type_id = project.content_type_id if project else None
    return VideoParams(
        video_subject=topic,
        video_script=brief.script,
        video_terms=brief.search_terms,
        match_materials_to_script=True,
        video_concat_mode=VideoConcatMode.sequential.value,
        video_aspect=VideoAspect.portrait.value,
        voice_name=_resolve_voice_name(project_id, brief),
        bgm_type="random",
        bgm_file=brief.bgm_file or "",
        quote_text=quote.text if quote else None,
        quote_attribution=(quote.attribution if quote and quote.is_quote else None),
        ai_image_fallback_enabled=_ai_image_fallback_allowed(content_type_id),
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


def _run_pipeline(
    project_id: int,
    topic: str,
    niche: str = "",
    revision_notes: Optional[str] = None,
    dossier: Optional[ResearchDossier] = None,
) -> None:
    try:
        cancellation.raise_if_cancelled(project_id)
        if not agent_base.is_configured():
            raise agent_base.AgentNotConfiguredError(
                "agents.anthropic_api_key is not configured; cannot run the Creative Director"
            )

        with session_scope() as session:
            project = session.get(VideoProject, project_id)
            content_type_id = project.content_type_id
            series_id = project.series_id
            stored_evidence = project.research_evidence

        content_type_info = _get_content_type_info(content_type_id)

        if content_type_info and content_type_info["research_required"] and dossier is None:
            if revision_notes and stored_evidence:
                # Revision retry (script-level, e.g. from retry_with_revision
                # or a QA "revise" verdict): the underlying facts/quote are
                # still valid, only the narrative needs a rewrite - reuse
                # what was already verified instead of re-researching (and
                # re-billing) on every revision loop.
                dossier = ResearchDossier.model_validate(stored_evidence)
            else:
                # Manual-topic project with a content type that still needs
                # verification (e.g. a human-typed quote or fact): the human's
                # topic is kept as-is and passed to the Researcher as a hint
                # to verify/ground, never overridden by what comes back.
                _set_status(project_id, ProjectStatus.RESEARCHING, topic=topic)
                dossier = _run_research(
                    project_id, content_type_id, topic, niche, series_id,
                    content_type_info["freshness_window_hours"],
                )
                _store_research_evidence(project_id, dossier)
                _set_status(project_id, ProjectStatus.RESEARCH_READY, topic=topic)

            if dossier.reduced_verification and content_type_id in _NEWS_CONTENT_TYPES:
                _set_status(
                    project_id,
                    ProjectStatus.FAILED,
                    failure_reason=(
                        "Research could not verify this story from independent sources within the "
                        "required freshness window; refusing to script an unverified news claim."
                    ),
                )
                _log_event(project_id, "Reduced verification on a news type is a hard fail", type_="error")
                return

        if revision_notes is None:
            # Originality gate (docs/DECISIONS_V3.md §2): runs once per
            # project, right before a real script gets written - never on a
            # revision re-entry (revision_notes is set), since that's the
            # same already-accepted topic being reworked, not a new idea.
            check = originality.evaluate_topic(project_id, content_type_id, series_id, topic, dossier)
            if check.rejected:
                _set_status(
                    project_id,
                    ProjectStatus.FAILED,
                    failure_reason=f"Originality check rejected this topic: {check.reason}",
                )
                _log_event(project_id, f"Originality check rejected the topic: {check.reason}", type_="error")
                return
            originality.commit_topic(project_id, content_type_id, series_id, topic, dossier)

        cancellation.raise_if_cancelled(project_id)
        _set_status(project_id, ProjectStatus.SCRIPTING, topic=topic)
        brief = _write_brief(project_id, topic, niche, revision_notes, dossier)
        _set_status(project_id, ProjectStatus.SCRIPT_READY)
        _log_event(project_id, "Creative Director produced a script and brief")

        cancellation.raise_if_cancelled(project_id)
        _produce_and_review(project_id, topic, niche, brief, dossier)
    except PipelineCancelled:
        _mark_cancelled(project_id)
    except Exception as exc:  # noqa: BLE001 - surface any pipeline failure on the project
        logger.exception(f"project {project_id} failed")
        _set_status(project_id, ProjectStatus.FAILED, failure_reason=str(exc))
        _log_event(project_id, f"Pipeline failed: {exc}", type_="error")


def _run_retrospective(project_id: int) -> None:
    """
    docs/DECISIONS_V3.md §3: one cheap Claude call per project, right after a
    human approves it, reading QA reports/revision notes/human edits at Final
    Review. Never fails the approval itself - this is purely a background
    learning-loop step - but failures are still logged as a visible
    AgentEvent, not swallowed.
    """
    try:
        with session_scope() as session:
            project = session.get(VideoProject, project_id)
            qa_reports = project.qa_reports or []
            human_edits = project.human_edits or []
            content_type_id = project.content_type_id
            script = (project.brief or {}).get("script", "")
            events = session.exec(
                select(AgentEvent)
                .where(AgentEvent.project_id == project_id)
                .where(AgentEvent.agent == "orchestrator")
                .where(AgentEvent.message.contains("requested:"))
            ).all()
            revision_notes_history = [e.message for e in events]

        from app.agents.retrospective import Retrospective

        lessons = Retrospective(project_id).run(
            qa_reports=qa_reports,
            human_edits=human_edits,
            revision_notes_history=revision_notes_history,
            script=script,
            content_type_id=content_type_id,
        )
        if not lessons:
            _log_event(project_id, "Retrospective found no actionable lessons for this project")
            return

        playbook.record_lessons(project_id, content_type_id, lessons)
        _log_event(project_id, f"Retrospective recorded {len(lessons)} lesson(s)")

        distilled_for = set()
        for lesson in lessons:
            key = (lesson.agent, content_type_id)
            if key in distilled_for:
                continue
            distilled_for.add(key)
            if playbook.is_distillation_due(lesson.agent, content_type_id):
                new_version = playbook.distill_playbook(lesson.agent, content_type_id)
                if new_version:
                    _log_event(
                        project_id,
                        f"Distilled a new playbook (v{new_version.version}) for {lesson.agent}/{content_type_id or 'any'}",
                    )
    except Exception as exc:  # noqa: BLE001 - must never affect the approval that triggered it
        logger.exception(f"project {project_id} retrospective failed")
        _log_event(project_id, f"Retrospective failed (approval is unaffected): {exc}", type_="error")


def _record_clip_index(project_id: int, final_state: dict) -> None:
    """
    Records this render's clips for the storyboard/per-scene-replacement
    bridge (docs/DECISIONS_V3.md §4). Same non-fatal treatment as storage
    materialization - a missing/failed clip index must not fail an
    otherwise-successful render, but is still surfaced, not swallowed.
    """
    try:
        storyboard.record_clips(project_id, final_state.get("materials") or [])
    except Exception as exc:  # noqa: BLE001 - must not fail the render pipeline
        logger.exception(f"project {project_id} clip index recording failed")
        _log_event(project_id, f"Storyboard clip index update failed (render output is unaffected): {exc}", type_="error")


def _materialize_project_storage(project_id: int) -> None:
    """
    Builds/refreshes the human-browsable storage/projects/... folder for this
    project (docs/DECISIONS_V3.md §1) after every successful render. A
    failure here must never fail an otherwise-successful render - it's a
    storage-layout convenience layered on top of the pipeline, not a
    correctness requirement for QA/approval - but per the "never silently
    ignore an error" rule it's still logged as a visible AgentEvent, not just
    a server-side log line.
    """
    try:
        project_storage.materialize_project(project_id)
    except Exception as exc:  # noqa: BLE001 - must not fail the render pipeline
        logger.exception(f"project {project_id} storage materialization failed")
        _log_event(project_id, f"Storage folder update failed (render output is unaffected): {exc}", type_="error")


def _produce_and_review(
    project_id: int, topic: str, niche: str, brief: CreativeBrief, dossier: Optional[ResearchDossier] = None
) -> None:
    """
    Renders `brief` and reviews it. On a "revise" verdict, either loops back
    through the full Creative Director (script-level problems) or - for
    material-only problems - regenerates just the search terms and re-enters
    here directly, without discarding a script that already worked. Shares
    one try/except with _run_pipeline via the caller; a materials-only retry
    calls back into this function rather than _run_pipeline so it never
    re-runs _write_brief.
    """
    cancellation.raise_if_cancelled(project_id)
    _set_status(project_id, ProjectStatus.PRODUCING)
    params = _video_params_from_brief(project_id, topic, brief)
    producer = Producer(project_id)
    final_state = producer.run(params)
    cancellation.raise_if_cancelled(project_id)
    _set_status(project_id, ProjectStatus.RENDERED)
    _record_clip_index(project_id, final_state)
    _materialize_project_storage(project_id)

    _set_status(project_id, ProjectStatus.QA_REVIEW)
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        video_path = project.video_path
        content_type_id = project.content_type_id
    reviewer = QualityReviewer(project_id)
    qa_report = reviewer.review(
        video_path=video_path,
        script=brief.script,
        subtitle_path=final_state.get("subtitle_path"),
        expected_audio_duration=final_state.get("audio_duration"),
        quote_or_lesson=brief.quote_or_lesson,
        research_dossier=dossier,
        prior_scripts=_recent_scripts(content_type_id, exclude_project_id=project_id),
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

    # overall == "revise", capped at max_revisions automatic loops (across
    # both revision paths combined) before escalating to a human.
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

    next_revision_count = current_revision_count + 1

    if qa_report.revision_target == "producer":
        # A material/visual problem, not a script problem: the script (and
        # its already-validated length, pacing, and voice) is kept as-is.
        # Redoing the whole script on every revision was discarding scripts
        # that already worked and gambling on an unvalidated new one instead
        # of fixing the actual flagged issue - this fixes the issue directly.
        _set_status(project_id, ProjectStatus.PRODUCING, revision_count=next_revision_count)
        _log_event(
            project_id,
            f"QA requested a materials-only revision ({next_revision_count}/{_max_revisions()}): "
            f"{qa_report.revision_notes}",
        )
        revised_brief = _revise_search_terms(project_id, niche, brief, qa_report.revision_notes or "")
        _produce_and_review(project_id, topic, niche, revised_brief, dossier)
        return

    _set_status(project_id, ProjectStatus.SCRIPTING, revision_count=next_revision_count)
    _log_event(
        project_id,
        f"QA requested a revision ({next_revision_count}/{_max_revisions()}): {qa_report.revision_notes}",
    )
    _run_pipeline(project_id, topic, niche, qa_report.revision_notes, dossier=dossier)


def _revise_search_terms(project_id: int, niche: str, brief: CreativeBrief, revision_notes: str) -> CreativeBrief:
    director = CreativeDirector(project_id)
    new_terms = director.revise_search_terms(script=brief.script, niche=niche, revision_notes=revision_notes)
    revised = brief.model_copy(update={"search_terms": new_terms})
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        project.brief = revised.model_dump()
        session.add(project)
        session.commit()
    return revised


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
    _materialize_project_storage(project_id)  # mirrors the new status into project.json
    threading.Thread(target=_run_retrospective, args=(project_id,), daemon=True).start()

    if not config.features.get("publishing_enabled", False):
        # Publishing is on hold (see [features].publishing_enabled in
        # config.toml): approving a project stops at APPROVED - assets stay
        # exactly where they are (docs/DECISIONS_V3.md §1) and the project
        # surfaces in an "Approved / Ready to publish" queue view. It no
        # longer auto-archives here; archiving now only happens via the
        # explicit mark_as_published() action below, once a human has
        # actually posted the video somewhere themselves.
        _log_event(
            project_id,
            "Publishing is paused (features.publishing_enabled=false); approved and ready to publish manually",
        )
        return

    _log_event(project_id, f"Approved for platforms: {', '.join(platforms)}")
    thread = threading.Thread(
        target=_run_publish, args=(project_id, video_path, package, platforms, thumbnail_path), daemon=True
    )
    thread.start()


def mark_as_published(project_id: int, platform_urls: Optional[List[dict]] = None) -> None:
    """
    docs/DECISIONS_V3.md §4: while publishing stays frozen, this is what
    "Publish" means - the human downloads the approved video, posts it
    manually wherever they choose, then records that it's live (optionally
    with the URL(s)) here. Reuses the existing published_posts/published_at
    columns the real Upload-Post path already writes, tagged source="manual"
    so Performance Analyst can tell them apart later if that ever matters,
    while still picking up any pasted YouTube URL for stats the same way.
    """
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        if project is None:
            raise ValueError(f"project {project_id} not found")
        if project.status != ProjectStatus.APPROVED.value:
            raise PermissionError(
                f"project {project_id} is not APPROVED (status={project.status}); nothing to mark as published"
            )
        existing_posts = list(project.published_posts or [])

    posts = [{**entry, "source": "manual"} for entry in (platform_urls or [])]
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        project.published_posts = existing_posts + posts
        session.add(project)
        session.commit()

    _set_status(project_id, ProjectStatus.PUBLISHED, published_at=utcnow())
    _log_event(project_id, f"Marked as published ({len(posts)} URL(s) recorded)")
    # Same PUBLISHED -> TRACKING transition _run_publish uses after a real
    # Upload-Post publish, so run_performance_checks (which only looks at
    # TRACKING projects) picks up any pasted YouTube URL the same way.
    _set_status(project_id, ProjectStatus.TRACKING)
    _materialize_project_storage(project_id)


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


_RETRYABLE_STATUSES = {ProjectStatus.FAILED.value, ProjectStatus.CANCELLED.value}


def retry_failed_project(project_id: int) -> None:
    """
    Plain retry with no revision notes, for infra-type failures (e.g. a
    transient render error) or a prior cancellation, rather than content
    feedback. Does not consume a revision slot.
    """
    with session_scope() as session:
        project = session.get(VideoProject, project_id)
        if project is None:
            raise ValueError(f"project {project_id} not found")
        if project.status not in _RETRYABLE_STATUSES:
            raise PermissionError(f"project {project_id} is not FAILED or CANCELLED (status={project.status})")
        topic = project.topic
        niche = project.niche or ""
        # Clear a prior cancellation request - otherwise the very first
        # checkpoint in the retried run would immediately cancel it again.
        project.cancel_requested = False
        session.add(project)
        session.commit()

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
