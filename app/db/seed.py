"""
Built-in content-type templates (docs/DESIGN_V2.md §2.10/§3). Seeded into the
ContentTypeTemplate table on first startup so a fresh install has sane
defaults; rows are then editable from Settings without a code change.
"""

from app.db.models import ContentTypeTemplate

DEFAULT_CONTENT_TYPES: list[dict] = [
    {
        "id": "motivational",
        "label": "Motivational",
        "default_duration_s": 50,
        "scriptcraft_overrides": {"structure": "story-arc", "cta_style": "woven-into-payoff"},
        "visual_strategy": {"stock_score_threshold": 6.0, "ai_gen_allowed": True, "ai_video_allowed": False},
        "voice_style": "warm, confident narrator",
        "subtitle_theme": "minimal_elegant",
        "music_palette": "cinematic_uplifting",
        "research_required": False,
        "freshness_window_hours": None,
        "series_capable": True,
        "default_quality_preset": "standard",
    },
    {
        "id": "fun_facts",
        "label": "Fun Facts",
        "default_duration_s": 40,
        "scriptcraft_overrides": {"structure": "episodic", "one_fact_per_scene": True},
        "visual_strategy": {"stock_score_threshold": 6.0, "ai_gen_allowed": True, "ai_video_allowed": False},
        "voice_style": "playful, energetic",
        "subtitle_theme": "bold_playful",
        "music_palette": "upbeat_bright",
        "research_required": False,
        "freshness_window_hours": None,
        "series_capable": True,
        "default_quality_preset": "standard",
    },
    {
        "id": "ai_news",
        "label": "AI News",
        "default_duration_s": 45,
        "scriptcraft_overrides": {"structure": "news-brief", "tone": "neutral-enthusiast"},
        "visual_strategy": {"stock_score_threshold": 6.5, "ai_gen_allowed": True, "ai_video_allowed": False},
        "voice_style": "neutral, enthusiastic",
        "subtitle_theme": "lower_third_source",
        "music_palette": "tech_ambient",
        "research_required": True,
        "freshness_window_hours": 24,
        "series_capable": False,
        "default_quality_preset": "standard",
    },
    {
        "id": "world_news",
        "label": "World News",
        "default_duration_s": 45,
        "scriptcraft_overrides": {"structure": "news-brief", "tone": "sober"},
        "visual_strategy": {"stock_score_threshold": 7.0, "ai_gen_allowed": False, "ai_video_allowed": False},
        "voice_style": "sober, measured",
        "subtitle_theme": "lower_third_source",
        "music_palette": "tech_ambient",
        "research_required": True,
        "freshness_window_hours": 24,
        "series_capable": False,
        "default_quality_preset": "standard",
    },
    {
        "id": "trending_now",
        "label": "Trending Now",
        "default_duration_s": 35,
        "scriptcraft_overrides": {"structure": "fast-hook", "pacing": "fastest"},
        "visual_strategy": {"stock_score_threshold": 5.5, "ai_gen_allowed": False, "ai_video_allowed": False},
        "voice_style": "energetic, current",
        "subtitle_theme": "bold_playful",
        "music_palette": "viral_trending",
        "research_required": False,
        "freshness_window_hours": 48,
        "series_capable": False,
        "default_quality_preset": "budget",
    },
]


def seed_content_types(session) -> None:
    """Insert any built-in template whose id isn't already present. Never
    overwrites an existing row, so user edits survive restarts/upgrades."""
    existing_ids = {row for row in session.exec(_select_ids())}
    for defaults in DEFAULT_CONTENT_TYPES:
        if defaults["id"] in existing_ids:
            continue
        session.add(ContentTypeTemplate(**defaults))
    session.commit()


def _select_ids():
    from sqlmodel import select

    return select(ContentTypeTemplate.id)
