#!/usr/bin/env python3
"""
🚀 Runner — Orchestrates daily scraping, storage, and notifications.

Usage:
    python runner.py                  # Run once immediately
    python runner.py --schedule       # Run on schedule (cron-like)
    python runner.py --dashboard      # Start the dashboard server
    python runner.py --schedule --dashboard  # Both
"""

import argparse
import json
import sys
import threading
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import config
import store
import notifier

# Import scrapers
sys.path.insert(0, str(Path(__file__).parent))
from scraper import (
    scrape_reddit, scrape_hackernews, scrape_producthunt,
    scrape_indiehackers, scrape_exploding_topics
)
from disruption_scanner import (
    scrape_g2, scrape_capterra, scrape_alternativeto,
    scrape_github_issues, scrape_public_boards, scrape_reddit_alternatives,
    score_all, save_html_report, AppOpportunity
)


def run_scan():
    """Execute a full scan cycle: scrape → store → notify → report."""
    print("\n" + "=" * 70)
    print(f"🚀 Starting scan — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    all_sources = config.SCANNER_SOURCES + config.SCRAPER_SOURCES
    run_id = store.start_run(all_sources)

    all_apps: list[AppOpportunity] = []
    errors = []

    # ── Disruption Scanner sources ────────────────────────────────────────
    scanner_map = {
        "g2": lambda: scrape_g2(
            max_rating=config.MAX_RATING,
            min_reviews=config.MIN_REVIEWS,
            limit=config.LIMIT_PER_SOURCE
        ),
        "capterra": lambda: scrape_capterra(
            max_rating=config.MAX_RATING,
            min_reviews=config.MIN_REVIEWS,
            limit=config.LIMIT_PER_SOURCE
        ),
        "alternativeto": lambda: scrape_alternativeto(limit=config.LIMIT_PER_SOURCE),
        "github": lambda: scrape_github_issues(limit=config.LIMIT_PER_SOURCE),
        "boards": lambda: scrape_public_boards(limit=config.LIMIT_PER_SOURCE),
    }

    for source in config.SCANNER_SOURCES:
        if source in scanner_map:
            try:
                apps = scanner_map[source]()
                all_apps.extend(apps)
            except Exception as e:
                errors.append(f"{source}: {e}")
                print(f"❌ Error in {source}: {e}")
                traceback.print_exc()

    # Also handle reddit alternatives from scanner
    if "reddit" in config.SCANNER_SOURCES:
        try:
            apps = scrape_reddit_alternatives(limit=config.LIMIT_PER_SOURCE)
            # Convert to AppOpportunity if needed
            all_apps.extend(apps)
        except Exception as e:
            errors.append(f"reddit-alts: {e}")

    # Score all disruption scanner results
    all_apps = score_all(all_apps)

    # ── Business Ideas Scraper sources ────────────────────────────────────
    ideas_scraper_map = {
        "reddit": lambda: scrape_reddit(limit=config.LIMIT_PER_SOURCE),
        "hn": lambda: scrape_hackernews(limit=config.LIMIT_PER_SOURCE),
        "producthunt": lambda: scrape_producthunt(limit=config.LIMIT_PER_SOURCE),
        "indiehackers": lambda: scrape_indiehackers(limit=config.LIMIT_PER_SOURCE),
        "exploding": lambda: scrape_exploding_topics(limit=config.LIMIT_PER_SOURCE),
    }

    ideas_posts = []
    for source in config.SCRAPER_SOURCES:
        if source in ideas_scraper_map:
            try:
                posts = ideas_scraper_map[source]()
                ideas_posts.extend(posts)
            except Exception as e:
                errors.append(f"ideas-{source}: {e}")
                print(f"❌ Error in ideas-{source}: {e}")

    # Convert ideas posts to AppOpportunity for unified storage
    for post in ideas_posts:
        post_dict = asdict(post) if hasattr(post, '__dataclass_fields__') else post
        app = AppOpportunity(
            name=post_dict.get("title", "")[:100],
            url=post_dict.get("url", ""),
            source=f"Ideas-{post_dict.get('source', 'unknown')}",
            category=post_dict.get("sub_source", ""),
            num_reviews=post_dict.get("score", 0),
            snippet=post_dict.get("snippet", "")[:200],
            negative_themes=post_dict.get("tags", []),
        )
        app.disruption_score = min(post_dict.get("score", 0) / 10, 50)  # Normalize
        all_apps.append(app)

    # ── Store results ─────────────────────────────────────────────────────
    print(f"\n💾 Storing {len(all_apps)} results...")
    new_count, updated_count = store.upsert_opportunities(all_apps)
    print(f"   🆕 {new_count} new | 🔄 {updated_count} updated")

    # ── Generate report ───────────────────────────────────────────────────
    today = datetime.now().strftime("%Y-%m-%d")
    report_path = config.REPORTS_DIR / f"report-{today}.html"

    # Only disruption scanner results for the HTML report
    disruption_apps = [a for a in all_apps if not a.source.startswith("Ideas-")]
    if disruption_apps:
        save_html_report(disruption_apps, report_path)
        print(f"📊 Report saved: {report_path}")

    # ── Notify ────────────────────────────────────────────────────────────
    print("\n🔔 Sending notifications...")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    new_apps = store.get_new_since(yesterday)
    top_apps = store.get_top_opportunities(limit=10, min_score=20)

    try:
        notifier.notify_all(new_count, updated_count, len(all_apps), top_apps, new_apps)
    except Exception as e:
        errors.append(f"notify: {e}")
        print(f"❌ Notification error: {e}")

    # ── Cleanup ───────────────────────────────────────────────────────────
    store.cleanup_old_data()

    # ── Finish run ────────────────────────────────────────────────────────
    error_str = "; ".join(errors) if errors else ""
    store.finish_run(run_id, len(all_apps), new_count, updated_count, error_str)

    print(f"\n✅ Scan complete!")
    print(f"   Total: {len(all_apps)} | New: {new_count} | Updated: {updated_count}")
    if errors:
        print(f"   ⚠️  Errors: {len(errors)}")

    return {
        "total": len(all_apps),
        "new": new_count,
        "updated": updated_count,
        "errors": errors,
    }


# ─── Scheduler ────────────────────────────────────────────────────────────────

def parse_cron(cron_expr: str) -> dict:
    """Parse a simple cron expression: 'minute hour day month weekday'."""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: {cron_expr}")
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "weekday": parts[4],
    }


def cron_matches(cron: dict, dt: datetime) -> bool:
    """Check if a datetime matches a cron schedule."""
    def matches_field(field_val: str, actual: int) -> bool:
        if field_val == "*":
            return True
        if "," in field_val:
            return actual in [int(x) for x in field_val.split(",")]
        if "/" in field_val:
            base, step = field_val.split("/")
            base = 0 if base == "*" else int(base)
            return (actual - base) % int(step) == 0
        if "-" in field_val:
            lo, hi = field_val.split("-")
            return int(lo) <= actual <= int(hi)
        return actual == int(field_val)

    return (
        matches_field(cron["minute"], dt.minute) and
        matches_field(cron["hour"], dt.hour) and
        matches_field(cron["day"], dt.day) and
        matches_field(cron["month"], dt.month) and
        matches_field(cron["weekday"], dt.weekday())  # 0=Monday
    )


def run_scheduler():
    """Simple cron-like scheduler that checks every minute."""
    cron = parse_cron(config.SCHEDULE_CRON)
    print(f"📅 Scheduler started — cron: {config.SCHEDULE_CRON} ({config.TIMEZONE})")
    print(f"   Next check every 60 seconds...")

    last_run_minute = None

    while True:
        try:
            now = datetime.now()
            current_minute = (now.hour, now.minute)

            if cron_matches(cron, now) and current_minute != last_run_minute:
                last_run_minute = current_minute
                print(f"\n⏰ Scheduled run triggered at {now.strftime('%H:%M')}")
                run_scan()
            else:
                # Show heartbeat every hour
                if now.minute == 0 and now.second < 61:
                    print(f"💓 Scheduler alive — {now.strftime('%Y-%m-%d %H:%M')}")

            time.sleep(60)

        except KeyboardInterrupt:
            print("\n👋 Scheduler stopped.")
            break
        except Exception as e:
            print(f"❌ Scheduler error: {e}")
            traceback.print_exc()
            time.sleep(120)  # Wait 2 min on error


# ─── Dashboard ────────────────────────────────────────────────────────────────

def run_dashboard():
    """Start a simple HTTP dashboard to view results."""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import urllib.parse

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path == "/" or path == "":
                self._serve_dashboard()
            elif path == "/api/stats":
                self._json_response(store.get_stats())
            elif path == "/api/top":
                params = urllib.parse.parse_qs(parsed.query)
                limit = int(params.get("limit", [50])[0])
                min_score = float(params.get("min_score", [0])[0])
                apps = store.get_top_opportunities(limit=limit, min_score=min_score)
                self._json_response(apps)
            elif path == "/api/new":
                yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
                apps = store.get_new_since(yesterday)
                self._json_response(apps)
            elif path == "/api/trending":
                apps = store.get_trending()
                self._json_response(apps)
            elif path == "/api/runs":
                runs = store.get_recent_runs()
                self._json_response(runs)
            elif path.startswith("/reports/"):
                self._serve_report(path)
            else:
                self.send_error(404)

        def _json_response(self, data):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data, default=str).encode())

        def _serve_report(self, path):
            filename = Path(path).name
            filepath = config.REPORTS_DIR / filename
            if filepath.exists() and filepath.suffix == ".html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(filepath.read_bytes())
            else:
                self.send_error(404)

        def _serve_dashboard(self):
            stats = store.get_stats()
            top = store.get_top_opportunities(limit=30, min_score=10)
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            new = store.get_new_since(yesterday)
            trending = store.get_trending(limit=10)
            runs = store.get_recent_runs(limit=10)

            # List available reports
            reports = sorted(config.REPORTS_DIR.glob("report-*.html"), reverse=True)[:10]

            html = _generate_dashboard_html(stats, top, new, trending, runs, reports)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())

        def log_message(self, format, *args):
            pass  # Suppress default logging

    server = HTTPServer((config.DASHBOARD_HOST, config.DASHBOARD_PORT), DashboardHandler)
    print(f"🌐 Dashboard running at http://localhost:{config.DASHBOARD_PORT}")
    server.serve_forever()


def _generate_dashboard_html(stats, top, new, trending, runs, reports) -> str:
    """Generate the dashboard HTML."""
    now = datetime.now().strftime("%B %d, %Y %H:%M")

    # Build top apps table rows
    top_rows = ""
    for app in top:
        score = app.get("disruption_score", 0)
        trend = app.get("score_trend", 0)
        trend_html = f'<span style="color:#6bcb77">↑{trend:+.0f}</span>' if trend > 0 else \
                     f'<span style="color:#ff6b6b">{trend:+.0f}</span>' if trend < 0 else ""
        color = "#ff6b6b" if score >= 40 else "#ffd93d" if score >= 20 else "#6bcb77"
        themes = app.get("negative_themes", [])
        if isinstance(themes, str):
            themes = json.loads(themes)

        top_rows += f"""
        <tr class="app-row" data-score="{score}" data-source="{app.get('source','')}"
            data-text="{app.get('name','').lower()} {' '.join(themes).lower()}">
            <td><a href="{app.get('url','#')}" target="_blank">{app.get('name','?')}</a></td>
            <td style="text-align:center;color:{color};font-weight:700">{score:.0f} {trend_html}</td>
            <td style="text-align:center">{'⭐ '+f"{app.get('rating',0):.1f}" if app.get('rating') else '—'}</td>
            <td style="text-align:center">{app.get('num_reviews',0) or '—'}</td>
            <td>{app.get('source','')}</td>
            <td style="font-size:0.8rem">{', '.join(themes[:2]) if themes else '—'}</td>
            <td style="font-size:0.8rem;color:#888">{app.get('first_seen','')[:10]}</td>
        </tr>"""

    # New apps
    new_rows = ""
    for app in new[:10]:
        score = app.get("disruption_score", 0)
        color = "#ff6b6b" if score >= 40 else "#ffd93d"
        new_rows += f"""
        <tr>
            <td><a href="{app.get('url','#')}" target="_blank">{app.get('name','?')}</a></td>
            <td style="color:{color};font-weight:700">{score:.0f}</td>
            <td>{app.get('source','')}</td>
        </tr>"""

    # Runs
    runs_rows = ""
    for run in runs:
        status_emoji = "✅" if run.get("status") == "success" else "❌" if run.get("status") == "error" else "⏳"
        runs_rows += f"""
        <tr>
            <td>{run.get('started_at','')[:16]}</td>
            <td>{status_emoji} {run.get('status','')}</td>
            <td>{run.get('total_found',0)}</td>
            <td style="color:#6bcb77">{run.get('new_count',0)}</td>
        </tr>"""

    # Reports list
    reports_links = ""
    for r in reports:
        reports_links += f'<a href="/reports/{r.name}" target="_blank" style="display:block;margin:4px 0;color:#4d96ff">{r.name}</a>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="300">
<title>🎯 Disruption Dashboard</title>
<style>
  :root {{ --bg:#0c0a1a;--s1:#1a1530;--s2:#2a2445;--text:#e8e4f0;--muted:#9890aa;
           --red:#ff6b6b;--yellow:#ffd93d;--green:#6bcb77;--blue:#4d96ff;--border:#3d3555; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:'Inter',-apple-system,sans-serif; background:var(--bg); color:var(--text); padding:1.5rem; }}
  .container {{ max-width:1400px; margin:0 auto; }}
  h1 {{ font-size:1.8rem; background:linear-gradient(135deg,var(--red),var(--yellow));
        -webkit-background-clip:text; -webkit-text-fill-color:transparent; margin-bottom:0.3rem; }}
  .sub {{ color:var(--muted); font-size:0.9rem; margin-bottom:1.5rem; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:1rem; margin-bottom:1.5rem; }}
  .card {{ background:var(--s1); border:1px solid var(--border); border-radius:12px; padding:1.2rem; text-align:center; }}
  .card-val {{ font-size:2rem; font-weight:700; }}
  .card-label {{ font-size:0.75rem; color:var(--muted); margin-top:0.2rem; }}
  .panel {{ background:var(--s1); border:1px solid var(--border); border-radius:12px; padding:1.2rem; margin-bottom:1.5rem; }}
  .panel h2 {{ font-size:1.1rem; margin-bottom:0.8rem; }}
  table {{ width:100%; border-collapse:collapse; }}
  th {{ text-align:left; padding:0.5rem; font-size:0.8rem; color:var(--muted); border-bottom:1px solid var(--border); }}
  td {{ padding:0.5rem; font-size:0.85rem; border-bottom:1px solid var(--s2); }}
  a {{ color:var(--blue); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
  .search {{ background:var(--bg); border:1px solid var(--border); color:var(--text); padding:0.5rem 1rem;
             border-radius:8px; width:100%; max-width:400px; margin-bottom:1rem; font-size:0.9rem; }}
  .search::placeholder {{ color:var(--muted); }}
  .cols {{ display:grid; grid-template-columns:1fr 350px; gap:1.5rem; }}
  @media(max-width:900px) {{ .cols {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class="container">
  <h1>🎯 Disruption Dashboard</h1>
  <p class="sub">Last updated: {now} — Auto-refreshes every 5 min</p>

  <div class="grid">
    <div class="card"><div class="card-val" style="color:var(--blue)">{stats.get('total',0)}</div><div class="card-label">Total Tracked</div></div>
    <div class="card"><div class="card-val" style="color:var(--red)">{stats.get('high_score',0)}</div><div class="card-label">🔥 High Score</div></div>
    <div class="card"><div class="card-val" style="color:var(--green)">{len(new)}</div><div class="card-label">🆕 New (24h)</div></div>
    <div class="card"><div class="card-val" style="color:var(--yellow)">{stats.get('runs',0)}</div><div class="card-label">Total Runs</div></div>
    <div class="card"><div class="card-val" style="color:var(--muted)">{stats.get('avg_score',0):.0f}</div><div class="card-label">Avg Score</div></div>
  </div>

  <div class="cols">
    <div>
      <div class="panel">
        <h2>🔥 Top Opportunities</h2>
        <input type="text" class="search" placeholder="🔍 Filter..." oninput="filterTable(this)">
        <table id="topTable">
          <tr><th>Name</th><th>Score</th><th>Rating</th><th>Reviews</th><th>Source</th><th>Pain Points</th><th>First Seen</th></tr>
          {top_rows}
        </table>
      </div>
    </div>
    <div>
      <div class="panel">
        <h2>🆕 New (Last 24h)</h2>
        <table>
          <tr><th>Name</th><th>Score</th><th>Source</th></tr>
          {new_rows or '<tr><td colspan="3" style="color:var(--muted)">No new opportunities</td></tr>'}
        </table>
      </div>
      <div class="panel">
        <h2>📅 Recent Runs</h2>
        <table>
          <tr><th>Date</th><th>Status</th><th>Found</th><th>New</th></tr>
          {runs_rows or '<tr><td colspan="4" style="color:var(--muted)">No runs yet</td></tr>'}
        </table>
      </div>
      <div class="panel">
        <h2>📊 Reports</h2>
        {reports_links or '<p style="color:var(--muted)">No reports yet — run a scan first</p>'}
      </div>
      <div class="panel">
        <h2>🔗 API Endpoints</h2>
        <a href="/api/stats">/api/stats</a>
        <a href="/api/top?limit=20">/api/top</a>
        <a href="/api/new">/api/new</a>
        <a href="/api/trending">/api/trending</a>
        <a href="/api/runs">/api/runs</a>
      </div>
    </div>
  </div>
</div>
<script>
function filterTable(input) {{
  const q = input.value.toLowerCase();
  document.querySelectorAll('#topTable .app-row').forEach(row => {{
    row.style.display = (row.dataset.text || '').includes(q) ? '' : 'none';
  }});
}}
</script>
</body></html>"""


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="🚀 Disruption Scanner Runner")
    parser.add_argument("--schedule", action="store_true", help="Run on schedule (cron)")
    parser.add_argument("--dashboard", action="store_true", help="Start web dashboard")
    parser.add_argument("--once", action="store_true", help="Run once and exit (default)")
    args = parser.parse_args()

    # Initialize database
    store.init_db()
    print(f"💾 Database: {config.DB_PATH}")

    if args.schedule and args.dashboard:
        # Run both: dashboard in main thread, scheduler in background
        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()
        # Run an initial scan immediately
        run_scan()
        run_dashboard()

    elif args.schedule:
        # Run initial scan, then schedule
        run_scan()
        run_scheduler()

    elif args.dashboard:
        run_dashboard()

    else:
        # Default: run once
        result = run_scan()
        print(f"\n📊 Dashboard available: python runner.py --dashboard")
        print(f"📅 Schedule daily: python runner.py --schedule")
        return result


if __name__ == "__main__":
    main()
