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
    # model's own knowledge (no independent source lookup yet - that's the
    # Researcher agent's job once it exists).
    quote_attribution_check: Optional[str] = None  # correct | incorrect | uncertain


class QAReport(BaseModel):
    overall: str  # pass | revise | fail
    technical_checks: List[TechnicalCheck]
    frame_findings: List[FrameFinding]
    content_policy_flags: List[str] = Field(default_factory=list)
    revision_target: Optional[str] = None
    revision_notes: Optional[str] = None


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
