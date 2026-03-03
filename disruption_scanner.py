"""
Disruption Scanner -- finds SaaS apps ripe for disruption.

Scrapes G2, Capterra, AlternativeTo, GitHub Issues, public feedback boards,
and Reddit "alternative to" threads.  Each public function returns
list[AppOpportunity] and never raises; on failure it prints a warning and
returns [].
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup
from scraper import _fetch_js

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class AppOpportunity:
    name: str
    url: str
    source: str
    category: str = ""
    rating: float = 0
    num_reviews: int = 0
    alternatives_count: int = 0
    disruption_score: float = 0
    negative_themes: list[str] = field(default_factory=list)
    feature_requests: list[str] = field(default_factory=list)
    pain_points: list[str] = field(default_factory=list)
    snippet: str = ""


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


def _fetch(url: str, *, timeout: int = 15, retries: int = 2) -> Optional[requests.Response]:
    """GET *url* with retries.  Returns None on any failure."""
    for attempt in range(1, retries + 1):
        try:
            resp = _SESSION.get(url, timeout=timeout)
            if resp.status_code == 429:
                wait = min(2 ** attempt, 10)
                print(f"[scanner] 429 rate-limited on {url}, waiting {wait}s ...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except Exception as exc:
            if attempt < retries:
                time.sleep(1)
            else:
                print(f"[scanner] fetch failed ({attempt}/{retries}): {url} -- {exc}")
    return None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_all(apps: list[AppOpportunity]) -> list[AppOpportunity]:
    """Compute disruption_score (0-100) for each app and sort descending."""
    for app in apps:
        # Low rating + high reviews: 35 pts
        rating_factor = max(0, (5.0 - app.rating) / 4.0)
        review_factor = min(1.0, app.num_reviews / 200)
        rating_pts = rating_factor * review_factor * 35

        # High alternatives count: 20 pts
        alt_pts = min(1.0, app.alternatives_count / 50) * 20

        # Negative theme density: 25 pts
        theme_pts = min(1.0, len(app.negative_themes) / 5) * 25

        # Feature request volume: 20 pts
        request_pts = min(1.0, len(app.feature_requests) / 5) * 20

        raw = rating_pts + alt_pts + theme_pts + request_pts
        app.disruption_score = max(0, min(100, raw))

    apps.sort(key=lambda a: a.disruption_score, reverse=True)
    return apps


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------


def save_html_report(apps: list[AppOpportunity], path: Path) -> None:
    """Generate a styled HTML report and write it to *path*."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    high = sum(1 for a in apps if a.disruption_score >= 40)
    medium = sum(1 for a in apps if 20 <= a.disruption_score < 40)
    total = len(apps)

    rows = ""
    for app in apps:
        score = app.disruption_score
        if score >= 40:
            color = "#ff6b6b"
        elif score >= 20:
            color = "#ffd93d"
        else:
            color = "#6bcb77"

        pain = ", ".join(app.pain_points[:3]) if app.pain_points else "-"
        features = ", ".join(app.feature_requests[:3]) if app.feature_requests else "-"

        rows += (
            f"<tr>"
            f'<td><a href="{app.url}" target="_blank">{app.name}</a></td>'
            f'<td style="color:{color};font-weight:700;text-align:center">{score:.0f}</td>'
            f'<td style="text-align:center">{app.rating:.1f}</td>'
            f'<td style="text-align:center">{app.num_reviews}</td>'
            f"<td>{app.source}</td>"
            f"<td>{pain}</td>"
            f"<td>{features}</td>"
            f"</tr>\n"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Disruption Report</title>
<style>
  body {{ background:#0c0a1a; color:#e8e4f0; font-family:sans-serif; padding:2rem; }}
  h1 {{ margin-bottom:.5rem; }}
  .stats {{ margin-bottom:1rem; color:#9890aa; }}
  table {{ width:100%; border-collapse:collapse; }}
  th {{ text-align:left; padding:.5rem; color:#9890aa; border-bottom:1px solid #3d3555; }}
  td {{ padding:.5rem; border-bottom:1px solid #2a2445; }}
  a {{ color:#4d96ff; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
</style>
</head>
<body>
<h1>Disruption Report</h1>
<div class="stats">High: {high} | Medium: {medium} | Total: {total}</div>
<table>
<tr><th>App</th><th>Score</th><th>Rating</th><th>Reviews</th><th>Source</th><th>Pain Points</th><th>Feature Requests</th></tr>
{rows}
</table>
</body>
</html>"""

    path.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. G2
# ---------------------------------------------------------------------------

_G2_CATEGORIES = [
    "crm-software",
    "project-management",
    "help-desk",
    "email-marketing",
    "accounting",
    "hr-software",
]


def scrape_g2(
    max_rating: float = 4.0,
    min_reviews: int = 20,
    limit: int = 30,
) -> list[AppOpportunity]:
    """Scrape G2 category pages for apps with low ratings and many reviews."""
    try:
        apps: list[AppOpportunity] = []

        for category in _G2_CATEGORIES:
            url = f"https://www.g2.com/categories/{category}"
            resp = _fetch(url)
            if resp is None:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            for product in soup.select("[data-product-id], .product-listing, .product-card"):
                name_el = product.find(
                    ["a", "h3", "div"],
                    class_=re.compile(r"product-name|product-title|name", re.IGNORECASE),
                )
                if not name_el:
                    name_el = product.find(["a", "h3", "span"])
                if not name_el:
                    continue

                name = name_el.get_text(strip=True)
                if not name:
                    continue

                # URL
                a_tag = name_el if name_el.name == "a" else name_el.find("a", href=True)
                href = a_tag["href"] if a_tag and a_tag.get("href") else ""
                product_url = href if href.startswith("http") else f"https://www.g2.com{href}"

                # Rating
                rating_el = product.find(
                    ["span", "div"],
                    class_=re.compile(r"rating|star", re.IGNORECASE),
                )
                try:
                    rating = float(rating_el.get_text(strip=True)) if rating_el else 0
                except (ValueError, TypeError):
                    rating = 0

                # Reviews
                review_el = product.find(
                    ["span", "a", "div"],
                    class_=re.compile(r"review-count|reviews|num-reviews", re.IGNORECASE),
                )
                try:
                    review_text = review_el.get_text(strip=True) if review_el else "0"
                    num_reviews = int(re.sub(r"[^\d]", "", review_text) or "0")
                except (ValueError, TypeError):
                    num_reviews = 0

                if rating > max_rating or num_reviews < min_reviews:
                    continue

                apps.append(AppOpportunity(
                    name=name,
                    url=product_url,
                    source="g2",
                    category=category,
                    rating=rating,
                    num_reviews=num_reviews,
                ))

                if len(apps) >= limit:
                    break

            if len(apps) >= limit:
                break

        return apps[:limit]

    except Exception as exc:
        print(f"[scanner] scrape_g2 error: {exc}")
        return []


# ---------------------------------------------------------------------------
# 2. Capterra
# ---------------------------------------------------------------------------

_CAPTERRA_CATEGORIES = [
    "crm-software",
    "project-management-software",
    "help-desk-software",
    "email-marketing-software",
    "accounting-software",
]


def scrape_capterra(
    max_rating: float = 4.0,
    min_reviews: int = 20,
    limit: int = 30,
) -> list[AppOpportunity]:
    """Scrape Capterra category pages for apps with low ratings and many reviews."""
    try:
        apps: list[AppOpportunity] = []

        for category in _CAPTERRA_CATEGORIES:
            url = f"https://www.capterra.com/categories/{category}/"
            resp = _fetch(url)
            if resp is None:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            for product in soup.select(".product-card, .listing-card, [data-testid='product']"):
                name_el = product.find(
                    ["a", "h2", "h3", "span"],
                    class_=re.compile(r"product-name|name|title", re.IGNORECASE),
                )
                if not name_el:
                    name_el = product.find(["a", "h2", "h3", "span"])
                if not name_el:
                    continue

                name = name_el.get_text(strip=True)
                if not name:
                    continue

                a_tag = name_el if name_el.name == "a" else name_el.find("a", href=True)
                href = a_tag["href"] if a_tag and a_tag.get("href") else ""
                product_url = href if href.startswith("http") else f"https://www.capterra.com{href}"

                rating_el = product.find(
                    ["span", "div"],
                    class_=re.compile(r"rating|star|overall", re.IGNORECASE),
                )
                try:
                    rating = float(rating_el.get_text(strip=True)) if rating_el else 0
                except (ValueError, TypeError):
                    rating = 0

                review_el = product.find(
                    ["span", "a", "div"],
                    class_=re.compile(r"review|count", re.IGNORECASE),
                )
                try:
                    review_text = review_el.get_text(strip=True) if review_el else "0"
                    num_reviews = int(re.sub(r"[^\d]", "", review_text) or "0")
                except (ValueError, TypeError):
                    num_reviews = 0

                if rating > max_rating or num_reviews < min_reviews:
                    continue

                apps.append(AppOpportunity(
                    name=name,
                    url=product_url,
                    source="capterra",
                    category=category,
                    rating=rating,
                    num_reviews=num_reviews,
                ))

                if len(apps) >= limit:
                    break

            if len(apps) >= limit:
                break

        return apps[:limit]

    except Exception as exc:
        print(f"[scanner] scrape_capterra error: {exc}")
        return []


# ---------------------------------------------------------------------------
# 3. AlternativeTo
# ---------------------------------------------------------------------------


_ALTERNATIVETO_SOFTWARE_RE = re.compile(
    r"(?:https?://alternativeto\.net)?/software/([^/]+)/", re.IGNORECASE
)


def _parse_sitemap_apps(xml_text: str, limit: int) -> list[AppOpportunity]:
    """Extract AppOpportunity entries from a sitemap XML string.

    Looks for ``<url><loc>`` entries whose URL matches the
    ``/software/<name>/`` pattern and derives the app name from the path.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    # Handle namespaced sitemaps (xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    apps: list[AppOpportunity] = []
    seen: set[str] = set()

    for url_el in root.iter(f"{ns}url"):
        loc_el = url_el.find(f"{ns}loc")
        if loc_el is None or not loc_el.text:
            continue

        loc = loc_el.text.strip()
        m = _ALTERNATIVETO_SOFTWARE_RE.search(loc)
        if not m:
            continue

        slug = m.group(1)
        if slug in seen:
            continue
        seen.add(slug)

        # Convert slug to display name: "my-cool-app" -> "My Cool App"
        name = slug.replace("-", " ").title()

        apps.append(AppOpportunity(
            name=name,
            url=loc,
            source="alternativeto",
            rating=0,
            num_reviews=0,
        ))

        if len(apps) >= limit:
            break

    return apps


def scrape_alternativeto(limit: int = 30) -> list[AppOpportunity]:
    """Scrape AlternativeTo browse page for software entries.

    Uses Playwright (JS rendering) as primary approach since AlternativeTo
    blocks plain HTTP requests from CI. Falls back to requests-based fetch.
    """
    try:
        apps: list[AppOpportunity] = []
        url = "https://alternativeto.net/browse/all/"

        # --- primary: Playwright (JS-rendered) ------------------------------
        html = _fetch_js(url)
        if html:
            soup = BeautifulSoup(html, "html.parser")
        else:
            resp = _fetch(url)
            if resp is None:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")

        # AlternativeTo lists apps as links to /software/<slug>/
        seen: set[str] = set()
        for link in soup.find_all("a", href=True):
            href = link["href"]
            m = _ALTERNATIVETO_SOFTWARE_RE.search(href)
            if not m:
                continue

            slug = m.group(1)
            if slug in seen:
                continue
            seen.add(slug)

            title = link.get_text(strip=True)
            name = title if title and len(title) >= 2 else slug.replace("-", " ").title()

            full_url = href if href.startswith("http") else f"https://alternativeto.net{href}"

            apps.append(AppOpportunity(
                name=name,
                url=full_url,
                source="alternativeto",
            ))

            if len(apps) >= limit:
                break

        return apps[:limit]

    except Exception as exc:
        print(f"[scanner] scrape_alternativeto error: {exc}")
        return []


# ---------------------------------------------------------------------------
# 4. GitHub Issues
# ---------------------------------------------------------------------------

_GITHUB_QUERIES = [
    "saas+tool",
    "crm",
    "project+management",
    "help+desk",
    "analytics+dashboard",
]


def scrape_github_issues(limit: int = 30) -> list[AppOpportunity]:
    """Find repos with many open issues via the GitHub search API."""
    try:
        apps: list[AppOpportunity] = []
        seen_names: set[str] = set()

        for query in _GITHUB_QUERIES:
            url = (
                f"https://api.github.com/search/repositories"
                f"?q={query}+language:python+language:javascript+language:typescript"
                f"&sort=help-wanted-issues&order=desc&per_page=10"
            )
            resp = _fetch(url)
            if resp is None:
                continue

            data = resp.json()
            items = data.get("items", [])

            for item in items:
                open_issues = item.get("open_issues_count", 0)
                if open_issues < 50:
                    continue

                full_name = item.get("full_name", "")
                if full_name in seen_names:
                    continue
                seen_names.add(full_name)

                apps.append(AppOpportunity(
                    name=full_name,
                    url=item.get("html_url", ""),
                    source="github",
                    category=query.replace("+", " "),
                    num_reviews=open_issues,
                    snippet=item.get("description", "") or "",
                ))

                if len(apps) >= limit:
                    break

            if len(apps) >= limit:
                break

        return apps[:limit]

    except Exception as exc:
        print(f"[scanner] scrape_github_issues error: {exc}")
        return []


# ---------------------------------------------------------------------------
# 5. Public feedback boards
# ---------------------------------------------------------------------------

_BOARD_DOMAINS = ["canny.io", "nolt.io", "uservoice.com"]


def scrape_public_boards(limit: int = 30) -> list[AppOpportunity]:
    """Search for public feedback boards on Canny, Nolt, and UserVoice."""
    try:
        apps: list[AppOpportunity] = []

        for domain in _BOARD_DOMAINS:
            url = (
                f"https://www.google.com/search"
                f"?q=site:{domain}+feature+request&num=10"
            )
            resp = _fetch(url)
            if resp is None:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            for link in soup.find_all("a", href=True):
                href = link["href"]
                if domain not in href:
                    continue

                title = link.get_text(strip=True)
                if not title or len(title) < 3:
                    continue

                # Clean Google redirect URLs
                if "/url?q=" in href:
                    href = href.split("/url?q=")[1].split("&")[0]

                apps.append(AppOpportunity(
                    name=title[:100],
                    url=href,
                    source="feedback-board",
                    category=domain,
                ))

                if len(apps) >= limit:
                    break

            if len(apps) >= limit:
                break

        return apps[:limit]

    except Exception as exc:
        print(f"[scanner] scrape_public_boards error: {exc}")
        return []


# ---------------------------------------------------------------------------
# 6. Reddit "alternative to" threads
# ---------------------------------------------------------------------------

_ALT_SUBREDDITS = ["SaaS", "selfhosted", "software", "sysadmin"]
_ALT_PATTERN = re.compile(r"alternative\s+to\s+(\w[\w\s]{1,30})", re.IGNORECASE)
_ATOM_NS = "http://www.w3.org/2005/Atom"


def scrape_reddit_alternatives(limit: int = 30) -> list[AppOpportunity]:
    """Search Reddit for 'alternative to X' threads via RSS feeds."""
    try:
        apps: list[AppOpportunity] = []
        seen_names: set[str] = set()

        for sub in _ALT_SUBREDDITS:
            url = (
                f"https://www.reddit.com/r/{sub}/search.rss"
                f"?q=alternative+to&restrict_sr=1&sort=new&limit=10"
            )
            resp = _fetch(url)
            if resp is None:
                continue

            root = ET.fromstring(resp.text)

            for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
                title_el = entry.find(f"{{{_ATOM_NS}}}title")
                link_el = entry.find(f"{{{_ATOM_NS}}}link")

                title = title_el.text if title_el is not None and title_el.text else ""
                post_url = link_el.get("href", "") if link_el is not None else ""

                match = _ALT_PATTERN.search(title)
                if not match:
                    continue

                app_name = match.group(1).strip()
                if not app_name or app_name.lower() in seen_names:
                    continue
                seen_names.add(app_name.lower())

                apps.append(AppOpportunity(
                    name=app_name,
                    url=post_url,
                    source="reddit-alternatives",
                    category=f"r/{sub}",
                    snippet=title,
                ))

                if len(apps) >= limit:
                    break

            if len(apps) >= limit:
                break

        return apps[:limit]

    except Exception as exc:
        print(f"[scanner] scrape_reddit_alternatives error: {exc}")
        return []
