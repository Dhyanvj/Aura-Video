from fastapi import Request
from sqlmodel import select

from app.controllers.v1.base import new_router
from app.db import session_scope
from app.db.models import ProjectStatus, VideoProject
from app.services import budget, youtube_analytics
from app.utils import utils

router = new_router()


@router.get("/analytics", summary="Per-video performance metrics and budget status")
def get_analytics(request: Request):
    with session_scope() as session:
        projects = session.exec(
            select(VideoProject).where(
                VideoProject.status.in_([ProjectStatus.TRACKING.value, ProjectStatus.ARCHIVED.value])
            )
        ).all()
        videos = [
            {
                "project_id": p.id,
                "topic": p.topic,
                "niche": p.niche,
                "cost_usd": p.cost_usd,
                "published_at": p.published_at.isoformat() if p.published_at else None,
                "checkpoints": (p.analytics or {}).get("checkpoints", []),
            }
            for p in projects
        ]

    data = {
        "youtube_configured": youtube_analytics.is_configured(),
        "monthly_spend_usd": budget.current_month_spend(),
        "monthly_budget_cap_usd": budget.monthly_budget_cap(),
        "videos": videos,
    }
    return utils.get_response(200, data)
