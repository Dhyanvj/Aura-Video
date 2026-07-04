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


def start_scheduler() -> None:
    global _scheduler
    _scheduler = BackgroundScheduler()

    # Performance tracking runs independently of the daily-batch autopilot -
    # it just checks in on already-published videos.
    _scheduler.add_job(_run_performance_checks, IntervalTrigger(hours=1), id="performance_checks")

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
