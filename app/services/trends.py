import requests
from loguru import logger

from app.config import config


def youtube_signals(niche: str, max_results: int = 8) -> list[dict]:
    """
    Best-effort recent/popular video signals for a niche via YouTube Data API v3.
    Returns [] (never raises) if no API key is configured or the request fails.
    """
    api_key = config.trends.get("youtube_api_key", "")
    if not api_key or not niche.strip():
        return []

    try:
        response = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "q": niche,
                "type": "video",
                "order": "viewCount",
                "maxResults": max_results,
                "key": api_key,
            },
            timeout=10,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        return [
            {
                "title": item["snippet"]["title"],
                "channel": item["snippet"]["channelTitle"],
                "published_at": item["snippet"]["publishedAt"],
            }
            for item in items
        ]
    except Exception as exc:  # noqa: BLE001 - external API, never break the pipeline
        logger.warning(f"youtube trend lookup failed (best-effort, ignoring): {exc}")
        return []


def google_trends_related(niche: str) -> list[str]:
    """
    Best-effort related search queries via the unofficial pytrends library.
    Returns [] (never raises) on any failure - Google Trends has no official API
    and pytrends breaks without notice when Google changes its internal endpoints.
    """
    if not niche.strip():
        return []

    try:
        from pytrends.request import TrendReq

        pytrends = TrendReq(hl="en-US", tz=360)
        pytrends.build_payload([niche], timeframe="now 7-d")
        related = pytrends.related_queries()
        top = (related or {}).get(niche, {}).get("top")
        if top is None or top.empty:
            return []
        return top["query"].head(10).tolist()
    except Exception as exc:  # noqa: BLE001 - unofficial API, never break the pipeline
        logger.warning(f"google trends lookup failed (best-effort, ignoring): {exc}")
        return []
