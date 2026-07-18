"""
Built-in content-type templates (docs/DESIGN_V2.md §2.10/§3). Seeded into the
ContentTypeTemplate table on first startup so a fresh install has sane
defaults; rows are then editable from Settings without a code change.
"""

from app.db.models import ContentTypeTemplate

# Focus decision (motivational-only pivot): the platform now produces
# exactly one thing - motivational short-form video, in 5 sub-formats, each
# its own ContentTypeTemplate row so every existing content_type_id-keyed
# mechanism (playbooks, dedup, QA prompts, originality scoping) works
# per-sub-format for free. The other four legacy content types are disabled,
# not deleted, and the original umbrella "motivational" row is disabled too
# (superseded by motivational_quote) - all three are re-enableable from
# Settings with no code change, and existing projects of any of these types
# keep opening normally since their row still exists.
DEFAULT_CONTENT_TYPES: list[dict] = [
    {
        "id": "motivational_story",
        "label": "Motivational Story",
        "description": (
            "A true, verified micro-story with a narrative arc: relatable struggle, a turning "
            "point, and the lesson it leaves behind. Real people/events require dossier "
            "verification like news claims; composite stories must be framed as illustrative, "
            "never passed off as fact. Series-capable with 2-3 part cliffhanger arcs."
        ),
        "default_duration_s": 52,
        "scriptcraft_overrides": {
            "structure": "story_arc",
            "beats": ["struggle_setup", "turning_point", "lesson_close"],
            "turning_point_target_pct": 60,
        },
        "visual_strategy": {"stock_score_threshold": 6.5, "ai_gen_allowed": True, "ai_video_allowed": False},
        "voice_style": "warm, low, unhurried - measured storytelling delivery",
        "subtitle_theme": "cinematic_narrative",
        "music_palette": "cinematic_uplifting",
        "research_required": True,
        "freshness_window_hours": None,
        "series_capable": True,
        "default_quality_preset": "standard",
        "enabled": True,
    },
    {
        "id": "motivational_quote",
        "label": "Motivational Quote",
        "description": (
            "One real, attributed quote per episode - the quote shown on screen with a "
            "typographic treatment and a brief unpacking of what it means in practice."
        ),
        "default_duration_s": 30,
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
        "research_required": True,
        "freshness_window_hours": None,
        "series_capable": True,
        "default_quality_preset": "standard",
        "enabled": True,
    },
    {
        "id": "motivational_speech",
        "label": "Motivational Speech",
        "description": (
            "An original speech-style monologue - direct second-person address, building "
            "cadence, rhetorical repetition. Never reproduces or excerpts a real speech; a "
            "short verified quote may anchor the piece, but the monologue itself is original "
            "writing."
        ),
        "default_duration_s": 50,
        "scriptcraft_overrides": {
            "structure": "original_monologue",
            "beats": ["direct_address_open", "building_cadence", "rhetorical_repetition", "strongest_line_close"],
            "originality_required": True,
        },
        "visual_strategy": {"stock_score_threshold": 6.5, "ai_gen_allowed": True, "ai_video_allowed": False},
        "voice_style": "warm, low, unhurried - building intensity toward the close",
        "subtitle_theme": "speech_bold",
        "music_palette": "cinematic_uplifting",
        "research_required": False,
        "freshness_window_hours": None,
        "series_capable": False,
        "default_quality_preset": "standard",
        "enabled": True,
    },
    {
        "id": "motivational_words",
        "label": "Motivational Words",
        "description": (
            "5-8 powerful words or phrases delivered with long, deliberate pauses - "
            "full-screen animated typography over cinematic footage, music-forward mix."
        ),
        "default_duration_s": 22,
        "scriptcraft_overrides": {
            "structure": "minimal_words",
            "beats": ["word_sequence"],
            "pause_forward": True,
        },
        "visual_strategy": {"stock_score_threshold": 7.0, "ai_gen_allowed": True, "ai_video_allowed": False},
        "voice_style": "warm, low, unhurried - long deliberate pauses between words",
        "subtitle_theme": "typographic_fullscreen",
        "music_palette": "cinematic_uplifting",
        "research_required": False,
        "freshness_window_hours": None,
        "series_capable": False,
        "default_quality_preset": "standard",
        "enabled": True,
    },
    {
        "id": "motivational_lines",
        "label": "Motivational Lines",
        "description": (
            "3-5 punchy original one-liners in sequence, one per scene, rapid-fire pacing "
            "with bold text treatment."
        ),
        "default_duration_s": 28,
        "scriptcraft_overrides": {
            "structure": "line_sequence",
            "beats": ["line_sequence"],
            "pacing": "rapid_fire",
        },
        "visual_strategy": {"stock_score_threshold": 6.0, "ai_gen_allowed": True, "ai_video_allowed": False},
        "voice_style": "warm, confident - rapid-fire delivery, one line per scene",
        "subtitle_theme": "bold_playful",
        "music_palette": "cinematic_uplifting",
        "research_required": False,
        "freshness_window_hours": None,
        "series_capable": True,
        "default_quality_preset": "standard",
        "enabled": True,
    },
    {
        "id": "motivational",
        "label": "Motivational Quotes & Life Lessons (legacy)",
        "description": (
            "One real, attributed quote or concrete life lesson per episode - a relatable "
            "struggle, the quote/lesson shown on screen, what it means in practice, and a "
            "reflective close. Superseded by the 5 motivational sub-formats; kept disabled so "
            "existing projects still open normally."
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
        "research_required": True,
        "freshness_window_hours": None,
        "series_capable": True,
        "default_quality_preset": "standard",
        "enabled": False,
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
        "enabled": False,
    },
    {
        "id": "ai_news",
        "label": "AI News",
        "description": "A single specific AI news story from the last 24 hours, with sources.",
        "default_duration_s": 45,
        "scriptcraft_overrides": {"structure": "news-brief", "tone": "neutral-enthusiast"},
        "visual_strategy": {"stock_score_threshold": 6.5, "ai_gen_allowed": True, "ai_video_allowed": False},
        "voice_style": (
            "confident, energetic, engaging, upbeat - a professional news presenter delivering "
            "breaking developments"
        ),
        "subtitle_theme": "lower_third_source",
        "music_palette": "tech_energetic",
        "research_required": True,
        "freshness_window_hours": 24,
        "series_capable": False,
        "default_quality_preset": "standard",
        "enabled": False,
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
        "enabled": False,
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
        "enabled": False,
    },
]

# The 5 legacy ids disabled by the motivational-only pivot - used both by the
# fresh-install defaults above and by the upgrade migration below.
_MOTIVATIONAL_PIVOT_DISABLED_IDS = ("motivational", "fun_facts", "ai_news", "world_news", "trending_now")

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


# Part 4: voice_style/music_palette went from dead metadata to fields that
# actually drive TTS voice choice and BGM selection (see orchestrator.py),
# so AI News's placeholder "neutral, enthusiastic" / "tech_ambient" defaults
# needed sharpening into an actual energetic-presenter tone. Guarded on both
# fields still matching their exact old defaults, so a user's own edit to
# either one survives an upgrade untouched.
_AI_NEWS_TONE_MIGRATION = {
    "id": "ai_news",
    "if_voice_style_is": "neutral, enthusiastic",
    "if_music_palette_is": "tech_ambient",
    "set": {
        "voice_style": (
            "confident, energetic, engaging, upbeat - a professional news presenter delivering "
            "breaking developments"
        ),
        "music_palette": "tech_energetic",
    },
}


def seed_content_types(session) -> None:
    """Insert any built-in template whose id isn't already present. Never
    overwrites an existing row, so user edits survive restarts/upgrades."""
    existing_ids = {row for row in session.exec(_select_ids())}
    is_first_pivot_run = "motivational_story" not in existing_ids
    for defaults in DEFAULT_CONTENT_TYPES:
        if defaults["id"] in existing_ids:
            continue
        session.add(ContentTypeTemplate(**defaults))
    session.commit()

    _apply_migrations(session)
    _apply_research_required_migration(session)
    _apply_ai_news_tone_migration(session)
    if is_first_pivot_run:
        _apply_motivational_pivot_migration(session)


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


def _apply_ai_news_tone_migration(session) -> None:
    from app.db.models import utcnow

    migration = _AI_NEWS_TONE_MIGRATION
    template = session.get(ContentTypeTemplate, migration["id"])
    if (
        template is None
        or template.voice_style != migration["if_voice_style_is"]
        or template.music_palette != migration["if_music_palette_is"]
    ):
        return
    for field, value in migration["set"].items():
        setattr(template, field, value)
    template.updated_at = utcnow()
    session.add(template)
    session.commit()


def _apply_motivational_pivot_migration(session) -> None:
    """Disable the 5 legacy content types, once, on the first startup after
    upgrading to the motivational-only pivot (fresh installs already seed
    them with enabled=False directly, see DEFAULT_CONTENT_TYPES above). Only
    called by seed_content_types() when "motivational_story" didn't exist yet
    this call - a one-time signal independent of the enabled field itself, so
    a human re-enabling one of these rows from Settings later is never
    undone by a later run of this same migration."""
    from app.db.models import utcnow

    for content_type_id in _MOTIVATIONAL_PIVOT_DISABLED_IDS:
        template = session.get(ContentTypeTemplate, content_type_id)
        if template is None:
            continue
        template.enabled = False
        template.updated_at = utcnow()
        session.add(template)
    session.commit()


def _select_ids():
    from sqlmodel import select

    return select(ContentTypeTemplate.id)
