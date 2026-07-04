import re
from typing import Optional

import requests
from loguru import logger

from app.config import config

_YOUTUBE_ID_KEYS = ("video_id", "youtube_video_id", "videoId")
_YOUTUBE_URL_PATTERN = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/))([A-Za-z0-9_-]{6,})")


def is_configured() -> bool:
    return bool(config.trends.get("youtube_api_key"))


def get_video_stats(video_id: str) -> Optional[dict]:
    """
    Best-effort view/like/comment lookup via YouTube Data API v3. Returns
    None (never raises) if not configured, the video isn't found, or the
    request fails.
    """
    api_key = config.trends.get("youtube_api_key", "")
    if not api_key or not video_id:
        return None

    try:
        response = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "statistics", "id": video_id, "key": api_key},
            timeout=10,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        if not items:
            return None
        stats = items[0].get("statistics", {})
        return {
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
        }
    except Exception as exc:  # noqa: BLE001 - external API, never break the pipeline
        logger.warning(f"youtube stats lookup failed (best-effort, ignoring): {exc}")
        return None


def extract_youtube_video_id(published_posts: Optional[list]) -> Optional[str]:
    """
    Upload-Post's response shape for a successful publish isn't rigidly
    documented, so this searches the response defensively for either a
    known id field or a YouTube URL anywhere in the payload.
    """
    if not published_posts:
        return None

    def _search(value) -> Optional[str]:
        if isinstance(value, dict):
            for key in _YOUTUBE_ID_KEYS:
                if isinstance(value.get(key), str) and value[key]:
                    return value[key]
            for v in value.values():
                found = _search(v)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = _search(item)
                if found:
                    return found
        elif isinstance(value, str):
            match = _YOUTUBE_URL_PATTERN.search(value)
            if match:
                return match.group(1)
        return None

    return _search(published_posts)
