from sqlmodel import select

from app.config import config
from app.db import session_scope
from app.db.models import VideoProject, utcnow


def current_month_spend() -> float:
    start_of_month = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with session_scope() as session:
        projects = session.exec(select(VideoProject).where(VideoProject.created_at >= start_of_month)).all()
    return sum(p.cost_usd or 0.0 for p in projects)


def monthly_budget_cap() -> float:
    return float(config.agents.get("monthly_budget_usd", 0) or 0)


def is_budget_exceeded() -> bool:
    cap = monthly_budget_cap()
    if cap <= 0:
        return False
    return current_month_spend() >= cap
