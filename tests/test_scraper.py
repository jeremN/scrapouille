"""Tests for scraper module."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Union
from unittest.mock import patch, MagicMock

import pytest

from scraper import (
    IdeaPost,
    _extract_tags,
    _fetch,
    scrape_reddit,
    scrape_hackernews,
    scrape_producthunt,
    scrape_indiehackers,
    scrape_exploding_topics,
)


# ---------------------------------------------------------------------------
# Helpers for building fake responses
# ---------------------------------------------------------------------------

def _make_response(content: str | dict, status_code: int = 200) -> MagicMock:
    """Build a fake requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    if isinstance(content, dict):
        resp.json.return_value = content
        resp.text = json.dumps(content)
    else:
        resp.text = content
        resp.json.side_effect = ValueError("not json")
    resp.raise_for_status.return_value = None
    return resp


# ---- Fake Reddit RSS payload --------------------------------------------

FAKE_REDDIT_RSS = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>I built a SaaS tool for analytics</title>
    <link href="https://www.reddit.com/r/SaaS/comments/abc/my_post/"/>
    <content type="html">&lt;p&gt;Here is my new analytics SaaS product.&lt;/p&gt;</content>
    <category term="SaaS" label="r/SaaS"/>
  </entry>
  <entry>
    <title>Launched my startup with AI automation</title>
    <link href="https://www.reddit.com/r/SaaS/comments/def/startup/"/>
    <content type="html">&lt;p&gt;Using AI to automate workflows.&lt;/p&gt;</content>
    <category term="SaaS" label="r/SaaS"/>
  </entry>
</feed>
"""

# ---- Fake HN Algolia payload --------------------------------------------

FAKE_HN_JSON = {
    "hits": [
        {
            "title": "Show HN: My AI startup tool",
            "url": "https://example.com/my-tool",
            "objectID": "12345",
            "points": 200,
            "story_text": "A tool built with machine learning.",
        },
        {
            "title": "Show HN: SaaS dashboard for devtools",
            "url": "https://example.com/dashboard",
            "objectID": "12346",
            "points": 80,
            "story_text": "",
        },
        {
            "title": "",  # empty title -- should be skipped
            "url": "https://example.com/empty",
            "objectID": "12347",
            "points": 10,
            "story_text": "",
        },
    ]
}

# ---- Fake Product Hunt HTML ----------------------------------------------

FAKE_PH_HTML = """
<html><body>
<a href="/posts/cool-ai-tool">Cool AI Tool</a>
<a href="/posts/startup-builder">Startup Builder</a>
<a href="/about">About Us</a>
<a href="/posts/another-product">Another Product Launch</a>
</body></html>
"""

# ---- Fake Indie Hackers HTML ---------------------------------------------

FAKE_IH_HTML = """
<html><body>
<div class="feed-item">
  <a href="/post/my-saas-story">My SaaS Story</a>
  <p class="body">I launched a SaaS product for small teams.</p>
</div>
<article class="post-card">
  <a href="/post/indie-journey">Indie Journey</a>
  <span class="excerpt">Building in public as an indie hacker.</span>
</article>
<div class="unrelated">
  <a href="/other">Other link</a>
</div>
</body></html>
"""

# ---- Fake Exploding Topics HTML ------------------------------------------

FAKE_ET_HTML = """
<html><body>
<div class="topic-card">
  <h3><a href="/topic/ai-agents">AI Agents</a></h3>
  <p class="desc">Growing trend in AI automation tools.</p>
</div>
<div class="trend-item">
  <a href="/topic/vertical-saas">Vertical SaaS</a>
</div>
<li class="card">
  <span>Edge Computing</span>
</li>
</body></html>
"""


# ===========================================================================
# Tests
# ===========================================================================


class TestIdeaPost:
    """Test the IdeaPost dataclass."""

    def test_ideapost_creation(self):
        post = IdeaPost(
            title="My Tool",
            url="https://example.com",
            source="test",
            sub_source="unit",
            score=42,
            snippet="A short description",
            tags=["saas", "ai"],
        )
        assert post.title == "My Tool"
        assert post.url == "https://example.com"
        assert post.source == "test"
        assert post.sub_source == "unit"
        assert post.score == 42
        assert post.snippet == "A short description"
        assert post.tags == ["saas", "ai"]

    def test_ideapost_default_tags(self):
        post = IdeaPost(
            title="X", url="https://x.com", source="s", sub_source="ss",
            score=0, snippet="",
        )
        assert post.tags == []


class TestExtractTags:
    """Test the _extract_tags helper."""

    def test_finds_matching_tags(self):
        tags = _extract_tags("I built a SaaS tool with AI")
        assert "saas" in tags
        assert "tool" in tags
        assert "ai" in tags

    def test_returns_empty_for_empty_string(self):
        assert _extract_tags("") == []

    def test_returns_empty_for_none(self):
        assert _extract_tags(None) == []

    def test_case_insensitive(self):
        tags = _extract_tags("Check out this SAAS API")
        assert "saas" in tags
        assert "api" in tags


class TestFetch:
    """Test the _fetch helper."""

    @patch("scraper._SESSION")
    def test_fetch_returns_none_on_bad_url(self, mock_session):
        mock_session.get.side_effect = Exception("connection error")
        result = _fetch("https://badurl.invalid", retries=1)
        assert result is None

    @patch("scraper._SESSION")
    def test_fetch_returns_response_on_success(self, mock_session):
        fake = _make_response({"ok": True})
        mock_session.get.return_value = fake
        result = _fetch("https://example.com", retries=1)
        assert result is not None
        assert result.json() == {"ok": True}

    @patch("scraper._SESSION")
    def test_fetch_retries_on_429(self, mock_session):
        rate_limited = MagicMock()
        rate_limited.status_code = 429

        success = _make_response({"ok": True})

        mock_session.get.side_effect = [rate_limited, success]
        result = _fetch("https://example.com", retries=2)
        assert result is not None
        assert mock_session.get.call_count == 2


class TestScrapeReddit:
    """Test scrape_reddit."""

    @patch("scraper._fetch")
    def test_scrape_reddit_parses_posts(self, mock_fetch):
        mock_fetch.return_value = _make_response(FAKE_REDDIT_RSS)
        posts = scrape_reddit(limit=10)

        assert len(posts) > 0
        assert all(isinstance(p, IdeaPost) for p in posts)
        assert all(p.source == "reddit" for p in posts)

        # RSS does not provide scores, so all should be 0
        assert all(p.score == 0 for p in posts)

        # First entry title should be present
        titles = [p.title for p in posts]
        assert any("SaaS" in t or "analytics" in t for t in titles)

    @patch("scraper._fetch")
    def test_scrape_reddit_returns_empty_on_failure(self, mock_fetch):
        mock_fetch.return_value = None
        posts = scrape_reddit(limit=10)
        assert posts == []

    @patch("scraper._fetch")
    def test_scrape_reddit_sub_source(self, mock_fetch):
        mock_fetch.return_value = _make_response(FAKE_REDDIT_RSS)
        posts = scrape_reddit(limit=5)
        assert all(p.sub_source.startswith("r/") for p in posts)

    @patch("scraper._fetch")
    def test_scrape_reddit_extracts_tags(self, mock_fetch):
        mock_fetch.return_value = _make_response(FAKE_REDDIT_RSS)
        posts = scrape_reddit(limit=50)
        # "SaaS" and "ai" should appear as tags from the RSS entries
        all_tags = [tag for p in posts for tag in p.tags]
        assert "saas" in all_tags
        assert "ai" in all_tags


class TestScrapeHackerNews:
    """Test scrape_hackernews."""

    @patch("scraper._fetch")
    def test_scrape_hackernews_parses_results(self, mock_fetch):
        mock_fetch.return_value = _make_response(FAKE_HN_JSON)
        posts = scrape_hackernews(limit=10)

        assert len(posts) > 0
        assert all(isinstance(p, IdeaPost) for p in posts)
        assert all(p.source == "hn" for p in posts)

        # Empty-title hit should be skipped
        titles = [p.title for p in posts]
        assert "" not in titles

    @patch("scraper._fetch")
    def test_scrape_hackernews_empty_on_failure(self, mock_fetch):
        mock_fetch.return_value = None
        posts = scrape_hackernews(limit=10)
        assert posts == []

    @patch("scraper._fetch")
    def test_scrape_hackernews_deduplicates(self, mock_fetch):
        # Return same payload for every query -- titles should be deduplicated
        mock_fetch.return_value = _make_response(FAKE_HN_JSON)
        posts = scrape_hackernews(limit=50)
        titles = [p.title for p in posts]
        assert len(titles) == len(set(titles))

    @patch("scraper._fetch")
    def test_scrape_hackernews_uses_hn_url_fallback(self, mock_fetch):
        payload = {
            "hits": [
                {
                    "title": "No URL hit",
                    "url": None,
                    "objectID": "99999",
                    "points": 10,
                    "story_text": "",
                },
            ]
        }
        mock_fetch.return_value = _make_response(payload)
        posts = scrape_hackernews(limit=5)
        assert len(posts) >= 1
        assert "news.ycombinator.com" in posts[0].url


class TestScrapeProductHunt:
    """Test scrape_producthunt."""

    @patch("scraper._fetch")
    def test_scrape_producthunt_returns_list(self, mock_fetch):
        mock_fetch.return_value = _make_response(FAKE_PH_HTML)
        posts = scrape_producthunt(limit=10)

        assert len(posts) == 3  # three /posts/ links
        assert all(isinstance(p, IdeaPost) for p in posts)
        assert all(p.source == "producthunt" for p in posts)
        assert all(p.sub_source == "daily" for p in posts)
        assert all("/posts/" in p.url for p in posts)

    @patch("scraper._fetch")
    def test_scrape_producthunt_empty_on_failure(self, mock_fetch):
        mock_fetch.return_value = None
        posts = scrape_producthunt(limit=10)
        assert posts == []

    @patch("scraper._fetch")
    def test_scrape_producthunt_respects_limit(self, mock_fetch):
        mock_fetch.return_value = _make_response(FAKE_PH_HTML)
        posts = scrape_producthunt(limit=1)
        assert len(posts) == 1


class TestScrapeIndieHackers:
    """Test scrape_indiehackers."""

    @patch("scraper._fetch")
    def test_scrape_indiehackers_returns_list(self, mock_fetch):
        mock_fetch.return_value = _make_response(FAKE_IH_HTML)
        posts = scrape_indiehackers(limit=10)

        assert len(posts) == 2
        assert all(isinstance(p, IdeaPost) for p in posts)
        assert all(p.source == "indiehackers" for p in posts)
        assert all(p.sub_source == "posts" for p in posts)

    @patch("scraper._fetch")
    def test_scrape_indiehackers_extracts_snippet(self, mock_fetch):
        mock_fetch.return_value = _make_response(FAKE_IH_HTML)
        posts = scrape_indiehackers(limit=10)
        # The first post should have a snippet from the <p class="body">
        snippets = [p.snippet for p in posts if p.snippet]
        assert len(snippets) >= 1

    @patch("scraper._fetch")
    def test_scrape_indiehackers_empty_on_failure(self, mock_fetch):
        mock_fetch.return_value = None
        posts = scrape_indiehackers(limit=10)
        assert posts == []


class TestScrapeExplodingTopics:
    """Test scrape_exploding_topics."""

    @patch("scraper._fetch")
    def test_scrape_exploding_topics_returns_list(self, mock_fetch):
        mock_fetch.return_value = _make_response(FAKE_ET_HTML)
        posts = scrape_exploding_topics(limit=10)

        assert len(posts) == 3
        assert all(isinstance(p, IdeaPost) for p in posts)
        assert all(p.source == "exploding" for p in posts)
        assert all(p.sub_source == "trending" for p in posts)

    @patch("scraper._fetch")
    def test_scrape_exploding_topics_extracts_tags(self, mock_fetch):
        mock_fetch.return_value = _make_response(FAKE_ET_HTML)
        posts = scrape_exploding_topics(limit=10)
        # "AI Agents" should produce "ai" tag
        ai_post = [p for p in posts if "AI" in p.title]
        assert len(ai_post) >= 1
        assert "ai" in ai_post[0].tags

    @patch("scraper._fetch")
    def test_scrape_exploding_topics_empty_on_failure(self, mock_fetch):
        mock_fetch.return_value = None
        posts = scrape_exploding_topics(limit=10)
        assert posts == []

    @patch("scraper._fetch")
    def test_scrape_exploding_topics_deduplicates_titles(self, mock_fetch):
        mock_fetch.return_value = _make_response(FAKE_ET_HTML)
        posts = scrape_exploding_topics(limit=10)
        titles = [p.title for p in posts]
        assert len(titles) == len(set(titles))
