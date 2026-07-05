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


class FrameFinding(BaseModel):
    frame_index: int
    matches_script: bool
    issues: List[str] = Field(default_factory=list)
    notes: str


class VisionReview(BaseModel):
    overall: str  # pass | revise | fail
    frame_findings: List[FrameFinding]
    content_policy_flags: List[str] = Field(default_factory=list)
    revision_target: Optional[str] = None  # creative_director | producer
    revision_notes: Optional[str] = None
    # Only set when a quote was supplied for the reviewer to check, from the
    # model's own training knowledge. This is independent of (and runs even
    # without) a Researcher dossier - it's a defense-in-depth check, not a
    # substitute for the dossier-based fact_check_flags below.
    quote_attribution_check: Optional[str] = None  # correct | incorrect | uncertain


class FactCheckFlag(BaseModel):
    sentence: str
    supported: bool
    note: str = ""


class FactCheckResult(BaseModel):
    flags: List[FactCheckFlag] = Field(default_factory=list)


class QAReport(BaseModel):
    overall: str  # pass | revise | fail
    technical_checks: List[TechnicalCheck]
    frame_findings: List[FrameFinding]
    content_policy_flags: List[str] = Field(default_factory=list)
    revision_target: Optional[str] = None
    revision_notes: Optional[str] = None
    fact_check_flags: List[FactCheckFlag] = Field(default_factory=list)
    # docs/DECISIONS_V3.md §2 script-repetition check: deterministic n-gram
    # overlap against the last 5 scripts of this content type - set only when
    # the overlap crossed the threshold, so it's None on every normal report.
    script_repetition_flag: Optional[str] = None


class OriginalityJudgment(BaseModel):
    """
    Output of the cheap Haiku-tier borderline-band check (docs/DECISIONS_V3.md
    §2): given two idea summaries, is this the same idea reworded, or a
    genuinely new angle?
    """

    same_idea: bool
    rationale: str = ""


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
