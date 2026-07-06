import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.config import config
from app.services import news_sources

_HN_RESPONSE = {
    "hits": [
        {"title": "New AI model released", "url": "https://example.com/a", "created_at": "2026-07-01", "objectID": "1"},
        {"title": "", "url": "https://example.com/b", "created_at": "2026-07-01", "objectID": "2"},
    ]
}

_RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Headline One</title><link>https://example.com/1</link><pubDate>Mon, 01 Jul 2026 00:00:00 GMT</pubDate></item>
<item><title>Headline Two</title><link>https://example.com/2</link><pubDate>Tue, 02 Jul 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""

_ATOM_XML = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom Entry</title><link href="https://example.com/atom"/><updated>2026-07-01T00:00:00Z</updated></entry>
</feed>"""

_GDELT_RESPONSE = {
    "articles": [
        {"title": "World event happens", "url": "https://example.com/w", "seendate": "20260701", "domain": "example.com"}
    ]
}

_FACT_CHECK_RESPONSE = {
    "claims": [
        {
            "text": "The sky is green",
            "claimReview": [{"textualRating": "False", "publisher": {"name": "FactCheckOrg"}, "url": "https://fc.example/1"}],
        }
    ]
}


class TestHackerNews(unittest.TestCase):
    def test_fetch_hn_stories_filters_empty_titles_and_maps_fields(self):
        mock_response = MagicMock()
        mock_response.json.return_value = _HN_RESPONSE
        mock_response.raise_for_status = lambda: None
        with patch("app.services.news_sources.requests.get", return_value=mock_response):
            stories = news_sources.fetch_hn_stories("AI")
        self.assertEqual(len(stories), 1)
        self.assertEqual(stories[0]["title"], "New AI model released")
        self.assertEqual(stories[0]["source"], "Hacker News")

    def test_fetch_hn_stories_empty_query_returns_empty(self):
        self.assertEqual(news_sources.fetch_hn_stories(""), [])

    def test_fetch_hn_stories_degrades_gracefully_on_network_failure(self):
        with patch("app.services.news_sources.requests.get", side_effect=ConnectionError("boom")):
            self.assertEqual(news_sources.fetch_hn_stories("AI"), [])

    def test_fetch_hn_stories_degrades_gracefully_on_malformed_response(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = lambda: None
        mock_response.json.side_effect = ValueError("not json")
        with patch("app.services.news_sources.requests.get", return_value=mock_response):
            self.assertEqual(news_sources.fetch_hn_stories("AI"), [])


class TestRssParsing(unittest.TestCase):
    def test_parses_rss_2_0_items(self):
        mock_response = MagicMock()
        mock_response.text = _RSS_XML
        mock_response.raise_for_status = lambda: None
        with patch("app.services.news_sources.requests.get", return_value=mock_response):
            items = news_sources.fetch_rss_headlines("https://example.com/feed")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "Headline One")
        self.assertEqual(items[0]["url"], "https://example.com/1")

    def test_parses_atom_entries(self):
        mock_response = MagicMock()
        mock_response.text = _ATOM_XML
        mock_response.raise_for_status = lambda: None
        with patch("app.services.news_sources.requests.get", return_value=mock_response):
            items = news_sources.fetch_rss_headlines("https://example.com/atom-feed")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Atom Entry")
        self.assertEqual(items[0]["url"], "https://example.com/atom")

    def test_malformed_xml_degrades_gracefully(self):
        mock_response = MagicMock()
        mock_response.text = "not xml at all <<<"
        mock_response.raise_for_status = lambda: None
        with patch("app.services.news_sources.requests.get", return_value=mock_response):
            self.assertEqual(news_sources.fetch_rss_headlines("https://example.com/feed"), [])

    def test_fetch_ai_news_signals_combines_hn_and_rss(self):
        hn_response = MagicMock()
        hn_response.json.return_value = _HN_RESPONSE
        hn_response.raise_for_status = lambda: None
        rss_response = MagicMock()
        rss_response.text = _RSS_XML
        rss_response.raise_for_status = lambda: None

        def fake_get(url, **kwargs):
            return hn_response if "algolia" in url else rss_response

        with patch("app.services.news_sources.requests.get", side_effect=fake_get):
            signals = news_sources.fetch_ai_news_signals("AI")
        # 1 HN story + 2 RSS items per feed x 2 feeds = 5
        self.assertEqual(len(signals), 5)


class TestGdelt(unittest.TestCase):
    def test_fetch_gdelt_articles_maps_fields(self):
        mock_response = MagicMock()
        mock_response.json.return_value = _GDELT_RESPONSE
        mock_response.raise_for_status = lambda: None
        with patch("app.services.news_sources.requests.get", return_value=mock_response):
            articles = news_sources.fetch_gdelt_articles("world news")
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "World event happens")

    def test_fetch_gdelt_degrades_gracefully(self):
        with patch("app.services.news_sources.requests.get", side_effect=ConnectionError("boom")):
            self.assertEqual(news_sources.fetch_gdelt_articles("world news"), [])


class TestGoogleFactCheck(unittest.TestCase):
    def setUp(self):
        self._original = dict(config.app)

    def tearDown(self):
        config.app.clear()
        config.app.update(self._original)

    def test_not_configured_returns_empty_without_a_network_call(self):
        config.app["google_fact_check_api_key"] = ""
        self.assertFalse(news_sources.is_fact_check_configured())
        with patch("app.services.news_sources.requests.get") as mock_get:
            self.assertEqual(news_sources.fetch_fact_checks("claim"), [])
        mock_get.assert_not_called()

    def test_configured_returns_parsed_claims(self):
        config.app["google_fact_check_api_key"] = "test-key"
        self.assertTrue(news_sources.is_fact_check_configured())
        mock_response = MagicMock()
        mock_response.json.return_value = _FACT_CHECK_RESPONSE
        mock_response.raise_for_status = lambda: None
        with patch("app.services.news_sources.requests.get", return_value=mock_response):
            results = news_sources.fetch_fact_checks("the sky is green")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["rating"], "False")
        self.assertEqual(results[0]["publisher"], "FactCheckOrg")

    def test_configured_but_network_failure_degrades_gracefully(self):
        config.app["google_fact_check_api_key"] = "test-key"
        with patch("app.services.news_sources.requests.get", side_effect=ConnectionError("boom")):
            self.assertEqual(news_sources.fetch_fact_checks("claim"), [])


if __name__ == "__main__":
    unittest.main()
