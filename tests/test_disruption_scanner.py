"""Tests for disruption_scanner module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from disruption_scanner import (
    AppOpportunity,
    score_all,
    save_html_report,
    scrape_g2,
    scrape_capterra,
    scrape_alternativeto,
    scrape_github_issues,
    scrape_public_boards,
    scrape_reddit_alternatives,
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


# ---------------------------------------------------------------------------
# Fake payloads
# ---------------------------------------------------------------------------

FAKE_G2_HTML = """
<html><body>
<div data-product-id="1" class="product-listing">
  <a class="product-name" href="/products/badcrm/reviews">BadCRM</a>
  <span class="rating">3.2</span>
  <span class="review-count">150 reviews</span>
</div>
<div data-product-id="2" class="product-listing">
  <a class="product-name" href="/products/goodtool/reviews">GoodTool</a>
  <span class="rating">4.8</span>
  <span class="review-count">300 reviews</span>
</div>
<div data-product-id="3" class="product-listing">
  <a class="product-name" href="/products/okaytool/reviews">OkayTool</a>
  <span class="rating">3.9</span>
  <span class="review-count">5 reviews</span>
</div>
</body></html>
"""

FAKE_CAPTERRA_HTML = """
<html><body>
<div class="product-card">
  <h3 class="product-name">SlowApp</h3>
  <span class="rating">3.5</span>
  <span class="review-count">80 reviews</span>
</div>
</body></html>
"""

FAKE_ALTERNATIVETO_HTML = """
<html><body>
<div class="app-item">
  <a class="app-name" href="/software/slack">Slack</a>
  <span class="alternatives">42 alternatives</span>
</div>
<div class="app-item">
  <a class="app-name" href="/software/trello">Trello</a>
  <span class="alternatives">28 alternatives</span>
</div>
</body></html>
"""

FAKE_GITHUB_JSON = {
    "items": [
        {
            "full_name": "org/big-saas-tool",
            "html_url": "https://github.com/org/big-saas-tool",
            "description": "A SaaS tool with many issues",
            "open_issues_count": 120,
            "stargazers_count": 5000,
        },
        {
            "full_name": "org/small-repo",
            "html_url": "https://github.com/org/small-repo",
            "description": "Few issues",
            "open_issues_count": 10,
            "stargazers_count": 50,
        },
        {
            "full_name": "org/another-tool",
            "html_url": "https://github.com/org/another-tool",
            "description": "Another tool with issues",
            "open_issues_count": 200,
            "stargazers_count": 3000,
        },
    ]
}

FAKE_REDDIT_ALT_JSON = {
    "data": {
        "children": [
            {
                "data": {
                    "title": "Looking for an alternative to Jira",
                    "permalink": "/r/SaaS/comments/abc/alt_jira/",
                }
            },
            {
                "data": {
                    "title": "Need an alternative to Slack?",
                    "permalink": "/r/SaaS/comments/def/alt_slack/",
                }
            },
            {
                "data": {
                    "title": "Random post without the pattern",
                    "permalink": "/r/SaaS/comments/ghi/random/",
                }
            },
        ]
    }
}


# ===========================================================================
# Tests
# ===========================================================================


class TestAppOpportunityDefaults:
    """Test the AppOpportunity dataclass."""

    def test_app_opportunity_defaults(self):
        app = AppOpportunity(name="Test", url="https://test.com", source="test")
        assert app.name == "Test"
        assert app.url == "https://test.com"
        assert app.source == "test"
        assert app.category == ""
        assert app.rating == 0
        assert app.num_reviews == 0
        assert app.alternatives_count == 0
        assert app.disruption_score == 0
        assert app.negative_themes == []
        assert app.feature_requests == []
        assert app.pain_points == []
        assert app.snippet == ""


class TestScoreAll:
    """Test score_all computation."""

    def test_score_all_computes_scores(self):
        bad_app = AppOpportunity(
            name="BadApp",
            url="https://bad.com",
            source="test",
            rating=2.0,
            num_reviews=500,
            negative_themes=["slow", "buggy", "expensive", "no support", "crashes"],
            feature_requests=["api", "export", "sso", "mobile", "integrations"],
        )
        ok_app = AppOpportunity(
            name="OkApp",
            url="https://ok.com",
            source="test",
            rating=4.5,
            num_reviews=10,
        )

        result = score_all([ok_app, bad_app])

        # BadApp should score higher than OkApp
        assert result[0].name == "BadApp"
        assert result[1].name == "OkApp"

        # Scores should be between 0 and 100
        for app in result:
            assert 0 <= app.disruption_score <= 100

        # BadApp should have a substantial score
        assert result[0].disruption_score > result[1].disruption_score

    def test_score_all_clamps_to_range(self):
        app = AppOpportunity(
            name="X", url="", source="test",
            rating=0, num_reviews=0,
        )
        result = score_all([app])
        assert 0 <= result[0].disruption_score <= 100

    def test_score_all_sorts_descending(self):
        apps = [
            AppOpportunity(name="Low", url="", source="t", rating=4.5, num_reviews=5),
            AppOpportunity(
                name="High", url="", source="t", rating=1.0, num_reviews=400,
                negative_themes=["a", "b", "c", "d", "e"],
                feature_requests=["x", "y", "z", "w", "v"],
            ),
        ]
        result = score_all(apps)
        scores = [a.disruption_score for a in result]
        assert scores == sorted(scores, reverse=True)


class TestSaveHtmlReport:
    """Test save_html_report."""

    def test_save_html_report(self, tmp_path):
        apps = [
            AppOpportunity(
                name="TestApp",
                url="https://test.com",
                source="g2",
                rating=3.0,
                num_reviews=100,
                disruption_score=55.0,
            ),
        ]
        report_path = tmp_path / "sub" / "report.html"
        save_html_report(apps, report_path)

        assert report_path.exists()
        content = report_path.read_text()
        assert "TestApp" in content
        assert "55" in content
        assert "#0c0a1a" in content  # dark theme background
        assert "Disruption Report" in content


class TestScrapeG2:
    """Test scrape_g2."""

    @patch("disruption_scanner._fetch")
    def test_scrape_g2_parses_html(self, mock_fetch):
        mock_fetch.return_value = _make_response(FAKE_G2_HTML)
        apps = scrape_g2(max_rating=4.0, min_reviews=20, limit=30)

        assert len(apps) > 0
        assert all(isinstance(a, AppOpportunity) for a in apps)
        assert all(a.source == "g2" for a in apps)

        # BadCRM (3.2, 150 reviews) should pass filters
        names = [a.name for a in apps]
        assert "BadCRM" in names

        # GoodTool (4.8 rating) should be excluded (above max_rating)
        assert "GoodTool" not in names

        # OkayTool (5 reviews) should be excluded (below min_reviews)
        assert "OkayTool" not in names

    @patch("disruption_scanner._fetch")
    def test_scrape_g2_empty_on_failure(self, mock_fetch):
        mock_fetch.return_value = None
        apps = scrape_g2()
        assert apps == []


class TestScrapeCapterra:
    """Test scrape_capterra."""

    @patch("disruption_scanner._fetch")
    def test_scrape_capterra_parses_html(self, mock_fetch):
        mock_fetch.return_value = _make_response(FAKE_CAPTERRA_HTML)
        apps = scrape_capterra(max_rating=4.0, min_reviews=20, limit=30)

        assert len(apps) > 0
        assert all(a.source == "capterra" for a in apps)
        names = [a.name for a in apps]
        assert "SlowApp" in names

    @patch("disruption_scanner._fetch")
    def test_scrape_capterra_empty_on_failure(self, mock_fetch):
        mock_fetch.return_value = None
        apps = scrape_capterra()
        assert apps == []


class TestScrapeAlternativeTo:
    """Test scrape_alternativeto."""

    @patch("disruption_scanner._fetch")
    def test_scrape_alternativeto_parses_html(self, mock_fetch):
        mock_fetch.return_value = _make_response(FAKE_ALTERNATIVETO_HTML)
        apps = scrape_alternativeto(limit=30)

        assert len(apps) == 2
        assert all(a.source == "alternativeto" for a in apps)
        names = [a.name for a in apps]
        assert "Slack" in names
        assert "Trello" in names
        assert apps[0].alternatives_count == 42

    @patch("disruption_scanner._fetch")
    def test_scrape_alternativeto_empty_on_failure(self, mock_fetch):
        mock_fetch.return_value = None
        apps = scrape_alternativeto()
        assert apps == []


class TestScrapeGithubIssues:
    """Test scrape_github_issues."""

    @patch("disruption_scanner._fetch")
    def test_scrape_github_issues_parses_api(self, mock_fetch):
        mock_fetch.return_value = _make_response(FAKE_GITHUB_JSON)
        apps = scrape_github_issues(limit=30)

        # org/small-repo has < 50 open issues so it should be excluded
        assert len(apps) >= 2
        assert all(isinstance(a, AppOpportunity) for a in apps)
        assert all(a.source == "github" for a in apps)

        names = [a.name for a in apps]
        assert "org/big-saas-tool" in names
        assert "org/another-tool" in names
        assert "org/small-repo" not in names

        # num_reviews should equal open_issues_count
        big = next(a for a in apps if a.name == "org/big-saas-tool")
        assert big.num_reviews == 120
        assert big.url == "https://github.com/org/big-saas-tool"
        assert big.snippet == "A SaaS tool with many issues"

    @patch("disruption_scanner._fetch")
    def test_scrape_github_issues_empty_on_failure(self, mock_fetch):
        mock_fetch.return_value = None
        apps = scrape_github_issues()
        assert apps == []


class TestScrapePublicBoards:
    """Test scrape_public_boards."""

    @patch("disruption_scanner._fetch")
    def test_scrape_public_boards_empty_on_failure(self, mock_fetch):
        mock_fetch.return_value = None
        apps = scrape_public_boards()
        assert apps == []


class TestScrapeRedditAlternatives:
    """Test scrape_reddit_alternatives."""

    @patch("disruption_scanner._fetch")
    def test_scrape_reddit_alternatives_parses(self, mock_fetch):
        mock_fetch.return_value = _make_response(FAKE_REDDIT_ALT_JSON)
        apps = scrape_reddit_alternatives(limit=30)

        assert len(apps) >= 2
        assert all(a.source == "reddit-alternatives" for a in apps)

        names = [a.name for a in apps]
        assert "Jira" in names
        assert "Slack" in names

    @patch("disruption_scanner._fetch")
    def test_scrape_reddit_alternatives_empty_on_failure(self, mock_fetch):
        mock_fetch.return_value = None
        apps = scrape_reddit_alternatives()
        assert apps == []
