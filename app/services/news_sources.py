"""
Free supplementary research signals (docs/DECISIONS_V3.md §6). These are
never the sole source of truth - the Researcher agent's Anthropic web search
call remains the primary, citation-producing path (app/agents/researcher.py).
Everything here just hands it a few concrete, timestamped candidate
headlines/claims to corroborate against, strengthening the freshness/fact
checks for AI News, World News, and quote/fact verification generally.

Every function degrades gracefully: a network failure, timeout, or
malformed response logs a warning and returns an empty list - never raises,
and never becomes a silent single point of failure (per the brief's rule -
if the Anthropic web search itself later fails, these were never the only
thing standing between a script and an unverified claim).
"""

import xml.etree.ElementTree as ET
from typing import List, Optional
from urllib.parse import quote_plus

import requests
from loguru import logger

from app.config import config

_REQUEST_TIMEOUT = 10

# AI News supplement (docs/DECISIONS_V3.md §6): Hacker News (free, no key,
# ideal for AI news) + a small set of curated tech RSS feeds.
_AI_NEWS_RSS_FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
]


def fetch_hn_stories(query: str, limit: int = 5) -> List[dict]:
    """Hacker News via the free Algolia search API - no key required."""
    if not query:
        return []
    try:
        response = requests.get(
            "https://hn.algolia.com/api/v1/search",
            params={"query": query, "tags": "story", "hitsPerPage": limit},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        hits = response.json().get("hits", [])
        return [
            {
                "title": hit.get("title"),
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                "published_at": hit.get("created_at"),
                "source": "Hacker News",
            }
            for hit in hits
            if hit.get("title")
        ]
    except Exception as exc:  # noqa: BLE001 - a supplementary signal failing must never block research
        logger.warning(f"Hacker News lookup failed for query={query!r}: {exc}")
        return []


def _parse_rss_items(xml_text: str, limit: int) -> List[dict]:
    items = []
    root = ET.fromstring(xml_text)
    # Handles both RSS 2.0 (<item>) and Atom (<entry>) without needing a new
    # dependency - just two tag names to check, via the stdlib parser.
    for item in root.iter():
        tag = item.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        title = next((c.text for c in item if c.tag.rsplit("}", 1)[-1] == "title"), None)
        link_el = next((c for c in item if c.tag.rsplit("}", 1)[-1] == "link"), None)
        link = (link_el.get("href") if link_el is not None and link_el.get("href") else (link_el.text if link_el is not None else None))
        published = next(
            (c.text for c in item if c.tag.rsplit("}", 1)[-1] in ("pubDate", "published", "updated")), None
        )
        if title:
            items.append({"title": title.strip(), "url": link, "published_at": published})
        if len(items) >= limit:
            break
    return items


def fetch_rss_headlines(feed_url: str, limit: int = 5) -> List[dict]:
    try:
        response = requests.get(feed_url, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
        items = _parse_rss_items(response.text, limit)
        for item in items:
            item["source"] = feed_url
        return items
    except Exception as exc:  # noqa: BLE001 - a supplementary signal failing must never block research
        logger.warning(f"RSS fetch failed for {feed_url!r}: {exc}")
        return []


def fetch_ai_news_signals(query: str, limit_per_source: int = 5) -> List[dict]:
    signals = fetch_hn_stories(query, limit_per_source)
    for feed_url in _AI_NEWS_RSS_FEEDS:
        signals.extend(fetch_rss_headlines(feed_url, limit_per_source))
    return signals


def fetch_gdelt_articles(query: str, limit: int = 5) -> List[dict]:
    """World News supplement (docs/DECISIONS_V3.md §6): GDELT's free DOC 2.0 API, no key required."""
    if not query:
        return []
    try:
        response = requests.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={"query": query, "format": "json", "maxrecords": limit, "sort": "datedesc"},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        articles = response.json().get("articles", [])
        return [
            {
                "title": a.get("title"),
                "url": a.get("url"),
                "published_at": a.get("seendate"),
                "source": a.get("domain"),
            }
            for a in articles
            if a.get("title")
        ]
    except Exception as exc:  # noqa: BLE001 - a supplementary signal failing must never block research
        logger.warning(f"GDELT lookup failed for query={query!r}: {exc}")
        return []


def is_fact_check_configured() -> bool:
    return bool(config.app.get("google_fact_check_api_key"))


def fetch_fact_checks(query: str, limit: int = 5) -> List[dict]:
    """
    Google Fact Check Tools API (docs/DECISIONS_V3.md §6): a free signal
    alongside the existing >=2-source verification, not a replacement for
    it - most niche facts/quotes won't have a fact-check entry at all, which
    is expected and not treated as a failure.
    """
    api_key = config.app.get("google_fact_check_api_key")
    if not api_key or not query:
        return []
    try:
        response = requests.get(
            "https://factchecktools.googleapis.com/v1alpha1/claims:search",
            params={"query": query, "pageSize": limit, "key": api_key},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        claims = response.json().get("claims", [])
        results = []
        for claim in claims:
            reviews = claim.get("claimReview") or []
            for review in reviews:
                results.append(
                    {
                        "claim": claim.get("text"),
                        "rating": review.get("textualRating"),
                        "publisher": (review.get("publisher") or {}).get("name"),
                        "url": review.get("url"),
                    }
                )
        return results[:limit]
    except Exception as exc:  # noqa: BLE001 - a supplementary signal failing must never block research
        logger.warning(f"Google Fact Check lookup failed for query={query!r}: {exc}")
        return []
