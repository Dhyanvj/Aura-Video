from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    # Naive but UTC: SQLite drops tzinfo on round-trip, so a tz-aware value
    # here would only match tz-aware values before the first save/reload,
    # then raise on any naive-vs-aware arithmetic against a reloaded row.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ProjectStatus(str, Enum):
    IDEA_PENDING = "IDEA_PENDING"
    IDEA_READY = "IDEA_READY"
    SCRIPTING = "SCRIPTING"
    SCRIPT_READY = "SCRIPT_READY"
    PRODUCING = "PRODUCING"
    RENDERED = "RENDERED"
    QA_REVIEW = "QA_REVIEW"
    QA_PASSED = "QA_PASSED"
    AWAITING_HUMAN_APPROVAL = "AWAITING_HUMAN_APPROVAL"
    APPROVED = "APPROVED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    TRACKING = "TRACKING"
    ARCHIVED = "ARCHIVED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


class VideoProject(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    status: str = Field(default=ProjectStatus.IDEA_PENDING.value, index=True)
    niche: Optional[str] = None
    topic: Optional[str] = None

    trend_report: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    brief: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    video_params: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    task_id: Optional[str] = Field(default=None, index=True)
    video_path: Optional[str] = None

    qa_reports: Optional[list] = Field(default=None, sa_column=Column(JSON))
    publish_package: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    published_posts: Optional[list] = Field(default=None, sa_column=Column(JSON))
    analytics: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    failure_reason: Optional[str] = None
    cost_usd: float = Field(default=0.0)
    revision_count: int = Field(default=0)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    published_at: Optional[datetime] = None


class AgentEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="videoproject.id", index=True)
    agent: str = Field(index=True)
    type: str  # thinking | tool_call | tool_result | output | error
    message: str = ""
    payload: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cost_usd: Optional[float] = None
    created_at: datetime = Field(default_factory=utcnow, index=True)
