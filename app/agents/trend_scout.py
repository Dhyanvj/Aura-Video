from typing import List, Optional

from app.agents.base import BaseAgent
from app.agents.schemas import TrendReport
from app.services import trends as trends_service
from app.utils import utils

_SYSTEM_PROMPT = """You are the Trend Scout for a short-form vertical video channel.
Given a niche, an audience description, real-world trend signals, and a list of
recently used topics to avoid repeating, propose 5-10 ranked video topic ideas.

For each idea, give: a title concept, why it's trending right now, evidence (facts,
links, or stats where you have them), the target emotion/hook, an estimated
competition level (low/medium/high), a suggested format (listicle/story/fact/how-to),
and a 0-100 opportunity score balancing trend strength against competition.

Never propose a topic that duplicates or closely overlaps one of the topics to avoid."""


class TrendScout(BaseAgent):
    agent_name = "trend_scout"

    def scout(self, niche: str, audience: str, recent_topics: Optional[List[str]] = None) -> TrendReport:
        recent_topics = recent_topics or []

        youtube = trends_service.youtube_signals(niche)
        google = trends_service.google_trends_related(niche)
        self.log_event(
            "tool_call",
            message=f"Gathered trend signals for niche {niche!r} "
            f"({len(youtube)} YouTube results, {len(google)} Google Trends queries)",
            payload={"youtube_signals": youtube, "google_trends_signals": google},
        )

        user = utils.to_json(
            {
                "niche": niche,
                "audience": audience,
                "topics_to_avoid": recent_topics,
                "youtube_signals": youtube,
                "google_trends_signals": google,
            }
        )
        return self.call_json(system=_SYSTEM_PROMPT, user=user, response_model=TrendReport)
