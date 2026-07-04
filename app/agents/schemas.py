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


class CreativeBrief(BaseModel):
    script: str
    search_terms: List[str]
    music_direction: str
    bgm_file: Optional[str] = None
    voice_recommendation: str
    subtitle_style: str
    metadata_draft: MetadataDraft
