from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from app.config import config

_scheduler: Optional[BackgroundScheduler] = None


def _run_scheduled_batch() -> None:
    from app.agents import orchestrator  # local import: avoid a circular import at module load time
    from app.services import budget

    if budget.is_budget_exceeded():
        logger.warning(
            f"scheduler: monthly budget cap (${budget.monthly_budget_cap():.2f}) reached "
            f"(${budget.current_month_spend():.2f} spent this month); skipping today's auto-trend batch"
        )
        return

    niche = config.trends.get("niche", "")
    audience = config.trends.get("audience", "")
    videos_per_day = int(config.schedule.get("videos_per_day", 1))
    logger.info(f"scheduler: creating {videos_per_day} auto-trend project(s) for niche={niche!r}")
    for _ in range(videos_per_day):
        orchestrator.start_auto_trend_project(niche=niche, audience=audience)


def _run_performance_checks() -> None:
    from app.agents import orchestrator  # local import: avoid a circular import at module load time

    orchestrator.run_performance_checks()


def _run_weekly_distillation() -> None:
    """
    docs/DECISIONS_V3.md §3: "weekly or every 10 projects, whichever first."
    The every-10-projects half fires deterministically right after each
    retrospective (app/agents/orchestrator.py::_run_retrospective); this is
    the weekly half, so a playbook still refreshes during a quiet week with
    fewer than 10 new lessons. distill_playbook re-derives from the full
    lesson history each time, so running it on a pair with no new lessons
    since last week is a harmless no-op-ish re-curation, not a bug.
    """
    from sqlmodel import select

    from app.db import session_scope
    from app.db.models import LessonLearned
    from app.services import playbook

    with session_scope() as session:
        pairs = set(session.exec(select(LessonLearned.agent, LessonLearned.content_type_id)).all())

    for agent, content_type_id in pairs:
        try:
            playbook.distill_playbook(agent, content_type_id)
        except Exception as exc:  # noqa: BLE001 - one pair's failure must not block the others
            logger.warning(f"weekly playbook distillation failed for agent={agent} content_type={content_type_id}: {exc}")


def start_scheduler() -> None:
    global _scheduler
    _scheduler = BackgroundScheduler()

    # Performance tracking runs independently of the daily-batch autopilot -
    # it just checks in on already-published videos.
    _scheduler.add_job(_run_performance_checks, IntervalTrigger(hours=1), id="performance_checks")
    _scheduler.add_job(_run_weekly_distillation, IntervalTrigger(days=7), id="weekly_playbook_distillation")

    if config.schedule.get("enabled", False):
        run_at = config.schedule.get("run_at", "09:00")
        try:
            hour_str, minute_str = run_at.split(":")
            hour, minute = int(hour_str), int(minute_str)
            _scheduler.add_job(_run_scheduled_batch, CronTrigger(hour=hour, minute=minute), id="daily_video_batch")
            logger.info(f"scheduler: daily batch enabled - {config.schedule.get('videos_per_day', 1)} video(s)/day at {run_at}")
        except (ValueError, AttributeError):
            logger.warning(f"invalid [schedule].run_at={run_at!r} (expected HH:MM); daily batch not scheduled")
    else:
        logger.info("scheduler: daily batch disabled ([schedule].enabled=false)")

    _scheduler.start()


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
