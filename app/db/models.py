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
    # v3 script-approval gate (docs/DECISIONS_V3.md): the human checkpoint
    # between a script existing and any production spend (TTS/downloads/
    # render) in manual approval mode. Automatic mode logs an auto-approval
    # AgentEvent and skips straight through instead of pausing here.
    AWAITING_SCRIPT_APPROVAL = "AWAITING_SCRIPT_APPROVAL"
    PRODUCING = "PRODUCING"
    RENDERED = "RENDERED"
    QA_REVIEW = "QA_REVIEW"
    QA_PASSED = "QA_PASSED"
    # QA escalation (incident fix, docs/DECISIONS_V3.md follow-up): reached
    # when the automatic revision budget is exhausted, or a QA finding
    # recurs with the same fingerprint across rounds (a revision cannot
    # resolve it) - never FAILED, which is reserved for actual pipeline
    # exceptions. The rendered video is preserved and playable; a human
    # resolves it via approve_despite_findings / request_changes_from_review
    # / reject_from_review (app/agents/orchestrator.py).
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    AWAITING_HUMAN_APPROVAL = "AWAITING_HUMAN_APPROVAL"
    APPROVED = "APPROVED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    TRACKING = "TRACKING"
    ARCHIVED = "ARCHIVED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    # Cooperative-cancellation terminal state (Recycle Bin: deleting an
    # in-flight project cancels it first - see app/services/cancellation.py).
    CANCELLED = "CANCELLED"
    # Soft-delete state (Recycle Bin). status_before_delete + deleted_at on
    # VideoProject carry what's needed to restore or purge it later.
    DELETED = "DELETED"


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

    # v3 learning loop (docs/DECISIONS_V3.md §3): {field, before, after}
    # entries captured whenever a human edits agent-drafted metadata at Final
    # Review - the highest-value signal for the retrospective pass, since
    # it's a direct record of "the AI got this wrong and a human fixed it."
    human_edits: Optional[list] = Field(default=None, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    published_at: Optional[datetime] = None

    # v3 script-approval gate: the mode this project was created under
    # ("manual" | "automatic"), snapshotted once at creation so an in-flight
    # project is never affected by a later Settings change (docs/DECISIONS_V3.md).
    approval_mode: Optional[str] = None
    # Script-stage regenerations at the AWAITING_SCRIPT_APPROVAL gate, tracked
    # separately from QA's revision_count/max_revisions - regenerating a
    # script the human hasn't approved yet is not a QA revision loop.
    script_revision_count: int = Field(default=0)

    # v3 Recycle Bin (docs/DECISIONS_V3.md): soft-delete fields. deleted_at
    # set means "in the bin"; status_before_delete is what Restore returns
    # the project to. cancel_requested is the cooperative-cancellation signal
    # checked at pipeline checkpoints (app/services/cancellation.py) so an
    # in-flight project is cancelled cleanly before it's soft-deleted.
    deleted_at: Optional[datetime] = Field(default=None, index=True)
    status_before_delete: Optional[str] = None
    cancel_requested: bool = Field(default=False)

    # QA escalation (incident fix). escalation_reason is a short human-
    # readable string set when entering NEEDS_HUMAN_REVIEW (revision limit
    # reached, or a repeated-fingerprint short-circuit) - distinct from
    # failure_reason, which stays reserved for actual pipeline exceptions.
    escalation_reason: Optional[str] = None
    # {"at": iso timestamp, "fingerprints": [...], "findings": [Finding.model_dump(), ...]}
    # entries, one per approve_despite_findings() call - the learning-loop
    # signal for "which QA findings did the human decide were fine to ship
    # anyway" (docs incident fix §2).
    overridden_findings: Optional[list] = Field(default=None, sa_column=Column(JSON))
    # Set at the script-approval gate when a quote/lesson centerpiece could
    # not be verified against the Researcher's dossier even after one free
    # rewrite attempt (docs incident fix §3) - surfaced to the human before
    # they approve the script, since a render hasn't happened yet and fixing
    # it here costs nothing. Cleared once a script's quote verifies cleanly.
    script_verification_warning: Optional[str] = None

    # Failed-project rescue (manual override, see app/services/rescue.py):
    # cached result of the last rescuability check, so the Failed list/card
    # can show a badge without re-running ffprobe on every page load. This
    # cache is NEVER trusted as the authorization decision for the actual
    # override - orchestrator.rescue_failed_project always re-checks fresh
    # at override time. None means "never checked."
    rescue_eligible: Optional[bool] = None
    rescue_checked_at: Optional[datetime] = None
    rescue_ineligible_reason: Optional[str] = None
    rescue_candidate_path: Optional[str] = None
    rescue_candidate_label: Optional[str] = None
    # [{"at", "from_status", "to_status", "failure_reason", "video_path",
    # "video_label", "script_edit_warning"}, ...] - one entry per successful
    # override, never erased. Feeds the retrospective learning loop the same
    # way overridden_findings does (a repeatedly-rescued failure type is a
    # signal the failure classification needs recalibrating).
    rescue_history: Optional[list] = Field(default=None, sa_column=Column(JSON))


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
    # Focus decision (motivational-only pivot): disabled types are hidden from
    # the New Video flow, Trend Scout, and the scheduler, but never deleted -
    # existing projects of that type stay viewable, and Settings can flip
    # this back on with no code change.
    enabled: bool = Field(default=True)
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


class ProjectClip(SQLModel, table=True):
    """
    The lightweight "clip-index bridge" (docs/DECISIONS_V3.md §4): one row
    per stock clip actually used in the current render, in narrative order.
    This is deliberately NOT the full scene-based Visual Director model from
    docs/DESIGN_V2.md (no vision scoring, no timestamps) - it's just enough
    to make the clips Final Review already downloads addressable and
    swappable. Rewritten (not appended to) after every render, so it always
    reflects the current final-video.mp4.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="videoproject.id", index=True)
    index: int
    search_term: str = ""
    provider: str = ""
    source_url: str = ""
    local_path: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class LessonLearned(SQLModel, table=True):
    """
    One lesson from a single project's retrospective (docs/DECISIONS_V3.md
    §3) - raw material for the periodic playbook distillation, never
    injected into a prompt directly.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="videoproject.id", index=True)
    agent: str = Field(index=True)
    content_type_id: Optional[str] = Field(default=None, index=True)
    what_worked: str = ""
    what_failed: str = ""
    actionable_rule: str = ""
    created_at: datetime = Field(default_factory=utcnow, index=True)


class Playbook(SQLModel, table=True):
    """
    A versioned, curated set of <=15 bullets for one (agent, content_type)
    pair (docs/DECISIONS_V3.md §3). Every edit - a distillation run, or a
    human toggling/editing a bullet in Settings - inserts a new row rather
    than mutating one in place, so "full version history with one-click
    rollback" is just "make a different version the active one," never a
    lossy in-place edit.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    agent: str = Field(index=True)
    content_type_id: Optional[str] = Field(default=None, index=True)
    version: int
    bullets: list = Field(sa_column=Column(JSON))  # list of PlaybookBullet.model_dump()
    is_active: bool = Field(default=True, index=True)
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
