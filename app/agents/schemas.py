from typing import List, Optional

from pydantic import BaseModel, Field


class TrendIdea(BaseModel):
    title: str
    why_trending: str
    evidence: List[str] = Field(default_factory=list)
    target_emotion: str
    estimated_competition: str  # low | medium | high
    suggested_format: str  # listicle | story | fact | how-to
    opportunity_score: int = Field(ge=0, le=100)


class TrendReport(BaseModel):
    ideas: List[TrendIdea] = Field(min_length=1, max_length=10)


class MetadataDraft(BaseModel):
    working_title: str
    hook_variants: List[str] = Field(default_factory=list)


class SourceCitation(BaseModel):
    url: str
    title: str
    published_or_accessed: str = ""  # a date/recency string if known (e.g. "2026-07-04" or "3 hours ago")


class KeyFact(BaseModel):
    statement: str
    citations: List[SourceCitation] = Field(default_factory=list)
    # verified = corroborated by >=2 independent sources; single-source = only
    # one source found; disputed = sources conflict; myth = a commonly
    # repeated claim the search itself contradicts - never used as a script's
    # spine fact.
    confidence: str = "single-source"  # verified | single-source | disputed | myth


class VerifiedQuote(BaseModel):
    """
    The Researcher's own verification of ONE specific quote/lesson centerpiece
    (motivational-type content), populated BEFORE the script approval gate -
    verification belongs pre-production, where re-checking costs nothing, not
    as a post-render QA re-litigation. Post-render QA (and the script-approval
    gate) trust this rather than independently re-deriving an opinion from
    scratch - see app/services/qa.py's verify_quote_against_dossier.
    """

    text: str  # exact wording the Researcher confirmed (or attempted to confirm)
    attribution: Optional[str] = None
    # verified = wording+attribution corroborated by >=2 independent sources;
    # unverified = could not confirm (recommend a life lesson instead);
    # disputed = sources actively conflict or debunk this attribution.
    verification_status: str = "unverified"  # verified | unverified | disputed
    sources: List[SourceCitation] = Field(default_factory=list)


class ResearchDossier(BaseModel):
    topic: str
    why_now: str = ""  # one line: why this topic, for a human skimming Project Detail
    key_facts: List[KeyFact] = Field(default_factory=list)
    disputed_points: List[str] = Field(default_factory=list)
    suggested_angle: str = ""
    sources: List[SourceCitation] = Field(default_factory=list)
    freshness_window_hours: Optional[int] = None
    # True whenever web search failed, returned nothing usable, or wasn't
    # available at all - content types that require verified sources (news)
    # treat this as an automatic QA fail rather than trusting an unverified
    # script.
    reduced_verification: bool = False
    # Motivational/Quotes only: the Researcher's verification of the ONE
    # quote/lesson centerpiece it recommends, if the type calls for a real
    # attributed quote at all. None when the type has no quote/lesson
    # centerpiece, or the Researcher recommended an (unattributed) life
    # lesson instead of a real quote.
    verified_quote: Optional[VerifiedQuote] = None


class QuoteOrLesson(BaseModel):
    is_quote: bool  # True = a real, attributed quote; False = an original life lesson (no attribution)
    text: str  # exact wording, spoken verbatim as part of the script
    attribution: Optional[str] = None  # e.g. "Marcus Aurelius" - required when is_quote is True
    # Self-assessed confidence that the wording+attribution pairing is
    # correct, from the model's own knowledge. There is no independent
    # source verification yet (that lands with the Researcher agent) - this
    # is an interim, best-effort signal QA uses to reject risky quotes.
    attribution_confidence: str = "high"  # high | medium | low


class CreativeBrief(BaseModel):
    script: str
    search_terms: List[str]
    music_direction: str
    bgm_file: Optional[str] = None
    voice_recommendation: str
    subtitle_style: str
    metadata_draft: MetadataDraft
    quote_or_lesson: Optional[QuoteOrLesson] = None
    # docs/DECISIONS_V3.md §2 hook variety: the opening technique used, so the
    # next script for this content type can be steered away from repeating
    # the same pattern back-to-back.
    hook_pattern: str = "other"  # question | bold_claim | statistic | story_cold_open | other
    opening_line: str = ""


class SearchTermsRevision(BaseModel):
    search_terms: List[str]


class TechnicalCheck(BaseModel):
    name: str
    passed: bool
    detail: str
    # Mapped in code from app.services.qa.severity_for_check, never model-
    # assigned - deterministic checks have a fixed, known severity.
    severity: str = "major"  # critical | major | minor


class FrameFinding(BaseModel):
    frame_index: int
    matches_script: bool
    issues: List[str] = Field(default_factory=list)
    notes: str
    # Model-assigned per fix 1's calibrated tiers (see quality_reviewer's
    # system prompt): critical = frame unusable/corrupted/watermarked, major =
    # a real mismatch/readability problem, minor = a polish preference that
    # must never block or trigger a revision on its own.
    severity: str = "minor"  # critical | major | minor
    justification: str = ""


class VisionReview(BaseModel):
    overall: str  # pass | revise | fail
    frame_findings: List[FrameFinding]
    content_policy_flags: List[str] = Field(default_factory=list)
    revision_target: Optional[str] = None  # creative_director | producer
    revision_notes: Optional[str] = None


class FactCheckFlag(BaseModel):
    sentence: str
    supported: bool
    note: str = ""


class FactCheckResult(BaseModel):
    flags: List[FactCheckFlag] = Field(default_factory=list)


class Finding(BaseModel):
    """
    One severity-tagged QA finding, deterministic or LLM-sourced, unified so
    the orchestrator's escalation logic and the Final Review UI can reason
    about "how bad" and "who can fix it" without re-deriving either from
    free-text revision_notes. See docs incident fix §1/§4.
    """

    category: str  # technical | visual | quote_attribution | fact_check | content_policy | script_repetition
    # Stable id for repeated-issue detection across QA rounds of the same
    # project (e.g. "technical:audio_present", "quote_attribution:marcus-aurelius").
    # Two findings with the same fingerprint across consecutive QA reports
    # mean a revision could not resolve it - see qa.py/orchestrator's
    # repeated-fingerprint short-circuit.
    fingerprint: str
    severity: str  # critical | major | minor
    message: str
    justification: str = ""
    # Who can actually fix this - creative_director (rewrite), producer
    # (re-render/different materials), researcher (needs more evidence, not a
    # rewrite), or None for a pure-informational minor finding.
    revision_target: Optional[str] = None
    # False only for "hard technical criticals" (missing/corrupt audio,
    # corrupted file) - these genuinely need a re-render and can never be
    # approved past at NEEDS_HUMAN_REVIEW, unlike every other finding type.
    overridable: bool = True


class QAReport(BaseModel):
    overall: str  # pass | pass_with_warnings | revise | fail
    technical_checks: List[TechnicalCheck]
    frame_findings: List[FrameFinding]
    content_policy_flags: List[str] = Field(default_factory=list)
    revision_target: Optional[str] = None  # creative_director | producer | researcher
    revision_notes: Optional[str] = None
    fact_check_flags: List[FactCheckFlag] = Field(default_factory=list)
    # docs/DECISIONS_V3.md §2 script-repetition check: deterministic n-gram
    # overlap against the last 5 scripts of this content type - set only when
    # the overlap crossed the threshold, so it's None on every normal report.
    script_repetition_flag: Optional[str] = None
    # Unified severity-tagged findings (see Finding) - the source of truth
    # `overall`/`revision_target` are computed from via
    # app.services.qa.aggregate_verdict/pick_revision_target. Empty on a
    # clean pass.
    findings: List[Finding] = Field(default_factory=list)


class OriginalityJudgment(BaseModel):
    """
    Output of the cheap Haiku-tier borderline-band check (docs/DECISIONS_V3.md
    §2): given two idea summaries, is this the same idea reworded, or a
    genuinely new angle?
    """

    same_idea: bool
    rationale: str = ""


_RETROSPECTIVE_AGENTS = "trend_scout | researcher | creative_director | quality_reviewer | publisher"


class RetrospectiveLesson(BaseModel):
    """
    One lesson from a completed project's retrospective (docs/DECISIONS_V3.md
    §3), attributed to the single agent it's actionable for.
    """

    agent: str  # trend_scout | researcher | creative_director | quality_reviewer | publisher
    what_worked: str = ""
    what_failed: str = ""
    actionable_rule: str


class RetrospectiveResult(BaseModel):
    lessons: List[RetrospectiveLesson] = Field(default_factory=list)


class PlaybookBullet(BaseModel):
    """One row of docs/DECISIONS_V3.md §3's distilled per-agent, per-content-type playbook."""

    text: str
    enabled: bool = True
    source_lesson_ids: List[int] = Field(default_factory=list)
    # Set at the distillation pass that follows a run of worse-than-average
    # QA scores while this bullet was active - surfaced for human review, not
    # auto-removed (never self-modifying without a human in the loop).
    flagged_for_review: bool = False


class PlaybookDistillation(BaseModel):
    bullets: List[PlaybookBullet] = Field(max_length=15)


class PlatformVariant(BaseModel):
    platform: str  # youtube_shorts | instagram_reels | tiktok
    caption: str
    hashtags: List[str] = Field(default_factory=list)


class PerformanceInsight(BaseModel):
    note: str  # one short "what worked / what didn't" sentence


class PublishPackage(BaseModel):
    title_options: List[str] = Field(min_length=3, max_length=3)
    description: str
    tags: List[str] = Field(min_length=10, max_length=15)
    category: str
    platform_variants: List[PlatformVariant]
    suggested_posting_time: str
    content_policy_flags: List[str] = Field(default_factory=list)
