from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from app.config import config

_scheduler: Optional[BackgroundScheduler] = None


def _run_scheduled_batch() -> None:
    from app.agents import orchestrator  # local import: avoid a circular import at module load time

    niche = config.trends.get("niche", "")
    audience = config.trends.get("audience", "")
    videos_per_day = int(config.schedule.get("videos_per_day", 1))
    logger.info(f"scheduler: creating {videos_per_day} auto-trend project(s) for niche={niche!r}")
    for _ in range(videos_per_day):
        orchestrator.start_auto_trend_project(niche=niche, audience=audience)


def start_scheduler() -> None:
    global _scheduler
    if not config.schedule.get("enabled", False):
        logger.info("scheduler disabled ([schedule].enabled=false)")
        return

    run_at = config.schedule.get("run_at", "09:00")
    try:
        hour_str, minute_str = run_at.split(":")
        hour, minute = int(hour_str), int(minute_str)
    except (ValueError, AttributeError):
        logger.warning(f"invalid [schedule].run_at={run_at!r} (expected HH:MM); scheduler not started")
        return

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(_run_scheduled_batch, CronTrigger(hour=hour, minute=minute), id="daily_video_batch")
    _scheduler.start()
    logger.info(f"scheduler started: {config.schedule.get('videos_per_day', 1)} video(s)/day at {run_at}")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
