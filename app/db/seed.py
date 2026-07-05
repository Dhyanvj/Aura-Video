"""
Built-in content-type templates (docs/DESIGN_V2.md §2.10/§3). Seeded into the
ContentTypeTemplate table on first startup so a fresh install has sane
defaults; rows are then editable from Settings without a code change.
"""

from app.db.models import ContentTypeTemplate

DEFAULT_CONTENT_TYPES: list[dict] = [
    {
        "id": "motivational",
        "label": "Motivational Quotes & Life Lessons",
        "description": (
            "One real, attributed quote or concrete life lesson per episode - a relatable "
            "struggle, the quote/lesson shown on screen, what it means in practice, and a "
            "reflective close."
        ),
        "default_duration_s": 45,
        "scriptcraft_overrides": {
            "structure": "quote_or_lesson_centered",
            "beats": [
                "relatable_struggle_hook",
                "quote_or_lesson_centerpiece",
                "practical_unpacking",
                "reflective_closing",
            ],
            "centerpiece_on_screen": True,
        },
        "visual_strategy": {"stock_score_threshold": 6.0, "ai_gen_allowed": True, "ai_video_allowed": False},
        "voice_style": "warm, confident narrator",
        "subtitle_theme": "minimal_elegant",
        "music_palette": "cinematic_uplifting",
        # No freshness window - a quote/lesson isn't time-sensitive - but it
        # still needs the Researcher's wording+attribution verification pass
        # before Creative Director writes around it.
        "research_required": True,
        "freshness_window_hours": None,
        "series_capable": True,
        "default_quality_preset": "standard",
    },
    {
        "id": "fun_facts",
        "label": "Fun Facts",
        "description": "One specific, verifiable fact per episode, told fast and punchy.",
        "default_duration_s": 40,
        "scriptcraft_overrides": {"structure": "episodic", "one_fact_per_scene": True},
        "visual_strategy": {"stock_score_threshold": 6.0, "ai_gen_allowed": True, "ai_video_allowed": False},
        "voice_style": "playful, energetic",
        "subtitle_theme": "bold_playful",
        "music_palette": "upbeat_bright",
        "research_required": True,
        "freshness_window_hours": None,
        "series_capable": True,
        "default_quality_preset": "standard",
    },
    {
        "id": "ai_news",
        "label": "AI News",
        "description": "A single specific AI news story from the last 24 hours, with sources.",
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
        "description": "A single verified world news story from the last 24 hours, sober tone.",
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
        "description": "A specific trend or event that's blowing up right now, turned around fast.",
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

# One-time content migrations for built-in rows that already exist in an
# upgraded database. seed_content_types() never overwrites a row a user has
# actually edited, so a developer-authored content revision (like the
# Motivational rework below) needs its own narrow, guarded update: apply it
# only if the row still matches the exact old default, never if the user
# has since customized it.
_MIGRATIONS = [
    {
        "id": "motivational",
        "if_label_is": "Motivational",
        "set": {
            "label": "Motivational Quotes & Life Lessons",
            "description": (
                "One real, attributed quote or concrete life lesson per episode - a relatable "
                "struggle, the quote/lesson shown on screen, what it means in practice, and a "
                "reflective close."
            ),
            "scriptcraft_overrides": {
                "structure": "quote_or_lesson_centered",
                "beats": [
                    "relatable_struggle_hook",
                    "quote_or_lesson_centerpiece",
                    "practical_unpacking",
                    "reflective_closing",
                ],
                "centerpiece_on_screen": True,
            },
        },
    },
]


# Part 3: Motivational and Fun Facts now also require a Researcher pass
# (quote/attribution verification, fact verification) even though neither
# has a freshness window. Guarded on "still the old False default" rather
# than a label match, since Part 2 already retitled the motivational row on
# upgraded databases - research_required has no other historical value to
# key off, so this is the best cheap signal available that a user hasn't
# deliberately turned it off by hand.
_RESEARCH_REQUIRED_MIGRATION_IDS = ("motivational", "fun_facts")


def seed_content_types(session) -> None:
    """Insert any built-in template whose id isn't already present. Never
    overwrites an existing row, so user edits survive restarts/upgrades."""
    existing_ids = {row for row in session.exec(_select_ids())}
    for defaults in DEFAULT_CONTENT_TYPES:
        if defaults["id"] in existing_ids:
            continue
        session.add(ContentTypeTemplate(**defaults))
    session.commit()

    _apply_migrations(session)
    _apply_research_required_migration(session)


def _apply_migrations(session) -> None:
    from app.db.models import utcnow

    for migration in _MIGRATIONS:
        template = session.get(ContentTypeTemplate, migration["id"])
        if template is None or template.label != migration["if_label_is"]:
            continue
        for field, value in migration["set"].items():
            setattr(template, field, value)
        template.updated_at = utcnow()
        session.add(template)
    session.commit()


def _apply_research_required_migration(session) -> None:
    from app.db.models import utcnow

    for content_type_id in _RESEARCH_REQUIRED_MIGRATION_IDS:
        template = session.get(ContentTypeTemplate, content_type_id)
        if template is None or template.research_required:
            continue
        template.research_required = True
        template.updated_at = utcnow()
        session.add(template)
    session.commit()


def _select_ids():
    from sqlmodel import select

    return select(ContentTypeTemplate.id)
