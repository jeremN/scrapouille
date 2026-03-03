"""
Scraper module -- collects business-idea posts from five sources.

Each public function returns list[IdeaPost] and never raises; on failure it
prints a warning and returns [].
"""

from __future__ import annotations

import atexit
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class IdeaPost:
    title: str
    url: str
    source: str        # "reddit", "hn", "producthunt", etc.
    sub_source: str    # subreddit name, "Show HN", category
    score: int         # upvotes / points
    snippet: str       # description excerpt
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
})

_KNOWN_TAGS = [
    "saas", "ai", "api", "tool", "startup", "automation", "no-code",
    "low-code", "marketplace", "fintech", "healthtech", "edtech",
    "analytics", "crm", "devtools", "open-source", "b2b", "b2c",
    "mobile", "chrome-extension", "plugin", "platform", "database",
    "cloud", "security", "productivity", "ecommerce", "payments",
    "machine-learning", "ml", "llm", "gpt", "blockchain", "web3",
    "crypto", "data", "dashboard", "workflow", "integration",
    "monitoring", "infrastructure", "cli", "sdk", "framework",
]


def _fetch(url: str, *, timeout: int = 15, retries: int = 2) -> Optional[requests.Response]:
    """GET *url* with retries.  Returns None on any failure."""
    for attempt in range(1, retries + 1):
        try:
            resp = _SESSION.get(url, timeout=timeout)
            if resp.status_code == 429:
                wait = min(2 ** attempt, 10)
                print(f"[scraper] 429 rate-limited on {url}, waiting {wait}s ...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except Exception as exc:
            if attempt < retries:
                time.sleep(1)
            else:
                print(f"[scraper] fetch failed ({attempt}/{retries}): {url} -- {exc}")
    return None


def _extract_tags(text: str) -> list[str]:
    """Return keyword tags found in *text* (case-insensitive)."""
    if not text:
        return []
    lowered = text.lower()
    return [tag for tag in _KNOWN_TAGS if tag in lowered]


# ---------------------------------------------------------------------------
# Playwright helper for JS-rendered pages
# ---------------------------------------------------------------------------

# Lazy-initialized browser instance
_BROWSER = None
_PLAYWRIGHT = None


def _fetch_js(url: str, wait_until: str = "networkidle", timeout: int = 15000) -> Optional[str]:
    """Fetch a URL using headless Chromium for JS-rendered pages. Returns HTML string or None."""
    global _BROWSER, _PLAYWRIGHT
    try:
        if _BROWSER is None:
            from playwright.sync_api import sync_playwright
            _PLAYWRIGHT = sync_playwright().start()
            _BROWSER = _PLAYWRIGHT.chromium.launch(headless=True)

        page = _BROWSER.new_page()
        page.set_extra_http_headers({"User-Agent": "Mozilla/5.0 (compatible; DisruptionScanner/1.0)"})
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout)
            content = page.content()
            return content
        finally:
            page.close()
    except Exception as e:
        print(f"  ⚠️ Playwright fetch failed for {url}: {e}")
        return None


def _cleanup_browser():
    global _BROWSER, _PLAYWRIGHT
    if _BROWSER:
        _BROWSER.close()
        _BROWSER = None
    if _PLAYWRIGHT:
        _PLAYWRIGHT.stop()
        _PLAYWRIGHT = None


atexit.register(_cleanup_browser)


# ---------------------------------------------------------------------------
# 1. Reddit
# ---------------------------------------------------------------------------

_REDDIT_SUBS = ["SaaS", "startups", "Entrepreneur", "microsaas", "indiehackers"]


_ATOM_NS = "http://www.w3.org/2005/Atom"


def scrape_reddit(limit: int = 30) -> list[IdeaPost]:
    """Fetch hot posts from business-related subreddits via RSS feeds."""
    try:
        per_sub = max(5, (limit // len(_REDDIT_SUBS)) + 5)
        posts: list[IdeaPost] = []

        for sub in _REDDIT_SUBS:
            url = f"https://www.reddit.com/r/{sub}/hot.rss?limit={per_sub}"
            resp = _fetch(url)
            if resp is None:
                continue

            root = ET.fromstring(resp.text)

            for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
                title_el = entry.find(f"{{{_ATOM_NS}}}title")
                link_el = entry.find(f"{{{_ATOM_NS}}}link")
                content_el = entry.find(f"{{{_ATOM_NS}}}content")

                title = title_el.text if title_el is not None and title_el.text else ""
                link = link_el.get("href", "") if link_el is not None else ""
                content = content_el.text if content_el is not None and content_el.text else ""

                if not title:
                    continue

                snippet = (content[:200] + "...") if len(content) > 200 else content

                posts.append(IdeaPost(
                    title=title,
                    url=link,
                    source="reddit",
                    sub_source=f"r/{sub}",
                    score=0,
                    snippet=snippet,
                    tags=_extract_tags(f"{title} {content}"),
                ))

        return posts[:limit]

    except Exception as exc:
        print(f"[scraper] scrape_reddit error: {exc}")
        return []


# ---------------------------------------------------------------------------
# 2. Hacker News (Algolia API)
# ---------------------------------------------------------------------------

_HN_QUERIES = ["Show HN", "startup launch", "SaaS"]


def scrape_hackernews(limit: int = 30) -> list[IdeaPost]:
    """Search HN via the Algolia API."""
    try:
        per_query = max(10, limit)
        seen_titles: set[str] = set()
        posts: list[IdeaPost] = []

        for query in _HN_QUERIES:
            url = (
                f"https://hn.algolia.com/api/v1/search"
                f"?query={requests.utils.quote(query)}"
                f"&tags=story&hitsPerPage={per_query}"
            )
            resp = _fetch(url)
            if resp is None:
                continue

            hits = resp.json().get("hits", [])
            for hit in hits:
                title = hit.get("title", "")
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)

                story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
                snippet = hit.get("story_text") or hit.get("comment_text") or ""
                if len(snippet) > 200:
                    snippet = snippet[:200] + "..."

                posts.append(IdeaPost(
                    title=title,
                    url=story_url,
                    source="hn",
                    sub_source=query,
                    score=hit.get("points", 0) or 0,
                    snippet=snippet,
                    tags=_extract_tags(f"{title} {snippet}"),
                ))

        posts.sort(key=lambda p: p.score, reverse=True)
        return posts[:limit]

    except Exception as exc:
        print(f"[scraper] scrape_hackernews error: {exc}")
        return []


# ---------------------------------------------------------------------------
# 3. Product Hunt (HTML)
# ---------------------------------------------------------------------------

def scrape_producthunt(limit: int = 30) -> list[IdeaPost]:
    """Scrape today's Product Hunt homepage for launched products."""
    try:
        html = _fetch_js("https://www.producthunt.com/")
        if html:
            soup = BeautifulSoup(html, "html.parser")
        else:
            resp = _fetch("https://www.producthunt.com/")
            if resp is None:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
        posts: list[IdeaPost] = []

        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "/posts/" not in href:
                continue

            title = link.get_text(strip=True)
            if not title or len(title) < 3:
                continue

            full_url = href if href.startswith("http") else f"https://www.producthunt.com{href}"

            # Avoid duplicates
            if any(p.url == full_url for p in posts):
                continue

            posts.append(IdeaPost(
                title=title,
                url=full_url,
                source="producthunt",
                sub_source="daily",
                score=0,
                snippet="",
                tags=_extract_tags(title),
            ))

            if len(posts) >= limit:
                break

        return posts[:limit]

    except Exception as exc:
        print(f"[scraper] scrape_producthunt error: {exc}")
        return []


# ---------------------------------------------------------------------------
# 4. Indie Hackers (HTML)
# ---------------------------------------------------------------------------

def scrape_indiehackers(limit: int = 30) -> list[IdeaPost]:
    """Scrape Indie Hackers posts feed."""
    try:
        html = _fetch_js("https://www.indiehackers.com/posts")
        if html:
            soup = BeautifulSoup(html, "html.parser")
        else:
            resp = _fetch("https://www.indiehackers.com/posts")
            if resp is None:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
        posts: list[IdeaPost] = []

        # Look for article/div elements with post-related classes
        candidates = soup.find_all(
            ["article", "div"],
            class_=re.compile(r"post|feed-item", re.IGNORECASE),
        )

        for el in candidates:
            # Try to find a link and title inside
            a_tag = el.find("a", href=True)
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            if not title or len(title) < 3:
                continue

            href = a_tag["href"]
            full_url = href if href.startswith("http") else f"https://www.indiehackers.com{href}"

            # Try to extract a snippet
            snippet_el = el.find(["p", "span"], class_=re.compile(r"body|excerpt|desc|snippet", re.IGNORECASE))
            snippet = snippet_el.get_text(strip=True)[:200] if snippet_el else ""

            posts.append(IdeaPost(
                title=title,
                url=full_url,
                source="indiehackers",
                sub_source="posts",
                score=0,
                snippet=snippet,
                tags=_extract_tags(f"{title} {snippet}"),
            ))

            if len(posts) >= limit:
                break

        return posts[:limit]

    except Exception as exc:
        print(f"[scraper] scrape_indiehackers error: {exc}")
        return []


# ---------------------------------------------------------------------------
# 5. Exploding Topics (HTML)
# ---------------------------------------------------------------------------

def scrape_exploding_topics(limit: int = 30) -> list[IdeaPost]:
    """Scrape trending topics from Exploding Topics."""
    try:
        html = _fetch_js("https://explodingtopics.com/")
        if html:
            soup = BeautifulSoup(html, "html.parser")
        else:
            resp = _fetch("https://explodingtopics.com/")
            if resp is None:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
        posts: list[IdeaPost] = []

        candidates = soup.find_all(
            ["div", "a", "article", "li"],
            class_=re.compile(r"topic|trend|card", re.IGNORECASE),
        )

        for el in candidates:
            # Find the main text / title
            title_el = el.find(["h2", "h3", "h4", "a", "span"])
            if not title_el:
                title = el.get_text(strip=True)
            else:
                title = title_el.get_text(strip=True)

            if not title or len(title) < 2:
                continue

            # Build URL
            a_tag = el.find("a", href=True) if el.name != "a" else el
            if a_tag and a_tag.get("href"):
                href = a_tag["href"]
                url = href if href.startswith("http") else f"https://explodingtopics.com{href}"
            else:
                url = "https://explodingtopics.com/"

            # Snippet
            desc_el = el.find(["p", "span"], class_=re.compile(r"desc|excerpt|snippet|body", re.IGNORECASE))
            snippet = desc_el.get_text(strip=True)[:200] if desc_el else ""

            # Avoid duplicate titles
            if any(p.title == title for p in posts):
                continue

            posts.append(IdeaPost(
                title=title,
                url=url,
                source="exploding",
                sub_source="trending",
                score=0,
                snippet=snippet,
                tags=_extract_tags(f"{title} {snippet}"),
            ))

            if len(posts) >= limit:
                break

        return posts[:limit]

    except Exception as exc:
        print(f"[scraper] scrape_exploding_topics error: {exc}")
        return []
