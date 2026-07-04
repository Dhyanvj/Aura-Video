from typing import List, Optional

from loguru import logger

from app.agents import base as agent_base
from app.agents.base import BaseAgent
from app.agents.schemas import PerformanceInsight
from app.services import youtube_analytics
from app.utils import utils

_SYSTEM_PROMPT = """You are the Performance Analyst for a short-form vertical video channel.
Given the video's script, niche, and its view/like/comment counts at this checkpoint
(plus any earlier checkpoints), write exactly ONE short sentence capturing what worked
or what didn't - concrete and specific enough to change what the Trend Scout proposes
next time (e.g. "hook framed as a question outperformed factual hooks in this niche" or
"engagement dropped after the midpoint, scripts may be running long for this format")."""


class PerformanceAnalyst(BaseAgent):
    agent_name = "performance_analyst"

    def check(
        self, video_id: str, checkpoint_hours: int, script: str, niche: str, history: Optional[List[dict]] = None
    ) -> Optional[dict]:
        """
        Pulls current stats for one checkpoint (24h/72h post-publish) and, if
        agents are configured, a short qualitative insight. Returns None
        (never raises) if analytics aren't configured or the video can't be
        found - callers should show "analytics not configured" rather than
        treat this as a failure.
        """
        stats = youtube_analytics.get_video_stats(video_id)
        if stats is None:
            self.log_event("output", message="Analytics not configured or video not found; skipping check")
            return None

        self.log_event(
            "tool_call",
            message=f"Checkpoint {checkpoint_hours}h stats: {stats['views']} views, "
            f"{stats['likes']} likes, {stats['comments']} comments",
            payload=stats,
        )

        note = None
        if agent_base.is_configured():
            try:
                payload = {"niche": niche, "script": script, "checkpoint_hours": checkpoint_hours, "stats": stats, "history": history or []}
                insight = self.call_json(system=_SYSTEM_PROMPT, user=utils.to_json(payload), response_model=PerformanceInsight)
                note = insight.note
            except Exception as exc:  # noqa: BLE001 - insight generation is best-effort
                logger.warning(f"performance insight generation failed (best-effort, ignoring): {exc}")

        return {"checkpoint_hours": checkpoint_hours, **stats, "note": note}
