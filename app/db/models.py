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
    RESEARCHING = "RESEARCHING"
    RESEARCH_READY = "RESEARCH_READY"
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
    research_evidence: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # ResearchDossier.model_dump()
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

    # v2: which content-type template and quality preset this project was
    # created with, and which series (if any) it belongs to. All nullable so
    # existing rows from before this migration still open normally.
    content_type_id: Optional[str] = Field(default=None, foreign_key="contenttypetemplate.id", index=True)
    quality_preset: Optional[str] = None
    series_id: Optional[int] = Field(default=None, foreign_key="series.id", index=True)
    episode_number: Optional[int] = None

    # v3: relative path (from storage/) of this project's human-browsable
    # folder - storage/projects/{content-type}/{date}-{slug}-{shortid}/. Set
    # once on first materialization and never changed afterward, so the
    # folder is stable for the project's life (docs/DECISIONS_V3.md §1).
    # Null for projects created before this migration, or task-only renders
    # from the legacy non-Agent-Studio API - both keep serving from
    # storage/tasks/{task_id}/ via the existing routes.
    storage_path: Optional[str] = None

    # v3 originality engine (docs/DECISIONS_V3.md §2): the opening technique
    # and first line this project's script used, so the next script for the
    # same content type can be steered away from repeating it.
    hook_pattern: Optional[str] = None
    opening_line: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    published_at: Optional[datetime] = None


class Series(SQLModel, table=True):
    """
    The "Series Bible": shared state that keeps episodes of a series
    consistent (same voice, same visual style, continuity) without needing a
    dedicated continuity agent - Creative Director reads it, Quality Reviewer
    validates against it.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    content_type_id: str = Field(foreign_key="contenttypetemplate.id", index=True)
    title: str
    style_guide: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # Empty until the founding episode's Creative Director recommends a valid
    # voice, at which point it's locked in for every future episode - see
    # orchestrator._resolve_voice_name.
    voice_id: str = Field(default="")
    voice_delivery_settings: dict = Field(default_factory=dict, sa_column=Column(JSON))
    music_palette: dict = Field(default_factory=dict, sa_column=Column(JSON))
    character_reference: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    pronunciation_dictionary: dict = Field(default_factory=dict, sa_column=Column(JSON))
    episode_counter: int = Field(default=0)
    rolling_summary: str = ""
    status: str = Field(default="active")  # active | paused | archived
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ContentTypeTemplate(SQLModel, table=True):
    """
    Data-driven "New Video" presets (Motivational, Fun Facts, AI News, World
    News, Trending Now). Seeded with built-in defaults on first startup and
    editable from Settings - a bug fix or new format is a row edit, not a
    code branch.
    """

    id: str = Field(primary_key=True)  # slug, e.g. "motivational"
    label: str
    description: str = ""  # shown on the New Video content-type card
    default_duration_s: int
    scriptcraft_overrides: dict = Field(default_factory=dict, sa_column=Column(JSON))
    visual_strategy: dict = Field(default_factory=dict, sa_column=Column(JSON))
    voice_style: str
    subtitle_theme: str
    music_palette: str
    research_required: bool = False
    freshness_window_hours: Optional[int] = None
    series_capable: bool = False
    default_quality_preset: str = "standard"  # budget | standard | cinematic
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TopicEmbedding(SQLModel, table=True):
    """
    One row per accepted project idea (docs/DECISIONS_V3.md §2): a local
    sentence-transformer embedding of "topic. angle." compared via brute-force
    cosine against every prior row for the same content type (or series, if
    the project belongs to one) to catch near-duplicate ideas before a script
    gets written.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="videoproject.id", index=True)
    content_type_id: Optional[str] = Field(default=None, index=True)
    series_id: Optional[int] = Field(default=None, index=True)
    text: str
    embedding: list = Field(sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow, index=True)


class UsedFact(SQLModel, table=True):
    """
    Fingerprint of a single verified fact/quote statement (docs/DECISIONS_V3.md
    §2 per-type rule): for Fun Facts and Motivational, a specific fact/quote is
    used once, ever, regardless of how differently a future script might word
    the surrounding topic.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    content_type_id: str = Field(index=True)
    fact_hash: str = Field(index=True)
    project_id: int = Field(foreign_key="videoproject.id")
    fact_text: str = ""
    created_at: datetime = Field(default_factory=utcnow)


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
