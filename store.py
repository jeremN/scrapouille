"""
💾 Storage — SQLite backend with diff detection.
Tracks opportunities over time and detects new/changed items.
"""

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import config


def get_db() -> sqlite3.Connection:
    """Get a database connection with WAL mode for concurrent reads."""
    db = sqlite3.connect(str(config.DB_PATH), timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = sqlite3.Row
    return db


def init_db():
    """Create tables if they don't exist."""
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            source TEXT NOT NULL,
            category TEXT DEFAULT '',
            rating REAL DEFAULT 0,
            num_reviews INTEGER DEFAULT 0,
            alternatives_count INTEGER DEFAULT 0,
            disruption_score REAL DEFAULT 0,
            negative_themes TEXT DEFAULT '[]',
            feature_requests TEXT DEFAULT '[]',
            pain_points TEXT DEFAULT '[]',
            snippet TEXT DEFAULT '',
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            last_score REAL DEFAULT 0,
            score_trend REAL DEFAULT 0,
            times_seen INTEGER DEFAULT 1,
            UNIQUE(name, source)
        );

        CREATE TABLE IF NOT EXISTS scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT DEFAULT 'running',
            total_found INTEGER DEFAULT 0,
            new_count INTEGER DEFAULT 0,
            updated_count INTEGER DEFAULT 0,
            sources TEXT DEFAULT '[]',
            error TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS score_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id INTEGER NOT NULL,
            score REAL NOT NULL,
            rating REAL DEFAULT 0,
            num_reviews INTEGER DEFAULT 0,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (opportunity_id) REFERENCES opportunities(id)
        );

        CREATE INDEX IF NOT EXISTS idx_opp_name ON opportunities(name);
        CREATE INDEX IF NOT EXISTS idx_opp_source ON opportunities(source);
        CREATE INDEX IF NOT EXISTS idx_opp_score ON opportunities(disruption_score DESC);
        CREATE INDEX IF NOT EXISTS idx_history_opp ON score_history(opportunity_id);
        CREATE INDEX IF NOT EXISTS idx_runs_date ON scan_runs(started_at DESC);
    """)
    db.commit()
    db.close()


# ─── Scan Runs ────────────────────────────────────────────────────────────────

def start_run(sources: list[str]) -> int:
    """Record the start of a scan run. Returns run_id."""
    db = get_db()
    cursor = db.execute(
        "INSERT INTO scan_runs (started_at, sources) VALUES (?, ?)",
        (datetime.now(timezone.utc).isoformat(), json.dumps(sources))
    )
    run_id = cursor.lastrowid
    db.commit()
    db.close()
    return run_id


def finish_run(run_id: int, total: int, new_count: int, updated: int, error: str = ""):
    """Record the end of a scan run."""
    db = get_db()
    status = "error" if error else "success"
    db.execute(
        """UPDATE scan_runs
           SET finished_at=?, status=?, total_found=?, new_count=?, updated_count=?, error=?
           WHERE id=?""",
        (datetime.now(timezone.utc).isoformat(), status, total, new_count, updated, error, run_id)
    )
    db.commit()
    db.close()


def get_recent_runs(limit: int = 30) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


# ─── Opportunities CRUD ──────────────────────────────────────────────────────

def upsert_opportunities(apps: list) -> tuple[int, int]:
    """
    Insert or update opportunities. Returns (new_count, updated_count).
    Detects new items and score changes.
    """
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    updated_count = 0

    for app in apps:
        app_dict = asdict(app) if hasattr(app, '__dataclass_fields__') else app

        name = app_dict.get("name", "")
        source = app_dict.get("source", "")
        score = app_dict.get("disruption_score", 0)

        if not name or not source:
            continue

        # Check if exists
        existing = db.execute(
            "SELECT id, disruption_score FROM opportunities WHERE name=? AND source=?",
            (name, source)
        ).fetchone()

        negative_themes = json.dumps(app_dict.get("negative_themes", []))
        feature_requests = json.dumps(app_dict.get("feature_requests", []))
        pain_points = json.dumps(app_dict.get("pain_points", []))

        if existing:
            # Update
            old_score = existing["disruption_score"]
            score_trend = score - old_score
            db.execute("""
                UPDATE opportunities SET
                    rating=?, num_reviews=?, alternatives_count=?, disruption_score=?,
                    negative_themes=?, feature_requests=?, pain_points=?, snippet=?,
                    last_seen=?, last_score=?, score_trend=?, times_seen=times_seen+1,
                    category=?, url=?
                WHERE id=?
            """, (
                app_dict.get("rating", 0), app_dict.get("num_reviews", 0),
                app_dict.get("alternatives_count", 0), score,
                negative_themes, feature_requests, pain_points,
                app_dict.get("snippet", ""), now, old_score, score_trend,
                app_dict.get("category", ""), app_dict.get("url", ""),
                existing["id"]
            ))

            # Record history
            db.execute(
                "INSERT INTO score_history (opportunity_id, score, rating, num_reviews, recorded_at) VALUES (?,?,?,?,?)",
                (existing["id"], score, app_dict.get("rating", 0), app_dict.get("num_reviews", 0), now)
            )
            updated_count += 1
        else:
            # New entry
            cursor = db.execute("""
                INSERT INTO opportunities
                    (name, url, source, category, rating, num_reviews, alternatives_count,
                     disruption_score, negative_themes, feature_requests, pain_points,
                     snippet, first_seen, last_seen, last_score, score_trend, times_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                name, app_dict.get("url", ""), source, app_dict.get("category", ""),
                app_dict.get("rating", 0), app_dict.get("num_reviews", 0),
                app_dict.get("alternatives_count", 0), score,
                negative_themes, feature_requests, pain_points,
                app_dict.get("snippet", ""), now, now, 0, 0, 1
            ))

            # Record initial history
            db.execute(
                "INSERT INTO score_history (opportunity_id, score, rating, num_reviews, recorded_at) VALUES (?,?,?,?,?)",
                (cursor.lastrowid, score, app_dict.get("rating", 0), app_dict.get("num_reviews", 0), now)
            )
            new_count += 1

    db.commit()
    db.close()
    return new_count, updated_count


def get_top_opportunities(limit: int = 50, min_score: float = 0,
                          source: str = "", category: str = "") -> list[dict]:
    """Get top-scored opportunities with optional filters."""
    db = get_db()
    query = "SELECT * FROM opportunities WHERE disruption_score >= ?"
    params: list = [min_score]

    if source:
        query += " AND source LIKE ?"
        params.append(f"%{source}%")
    if category:
        query += " AND category LIKE ?"
        params.append(f"%{category}%")

    query += " ORDER BY disruption_score DESC LIMIT ?"
    params.append(limit)

    rows = db.execute(query, params).fetchall()
    db.close()

    results = []
    for r in rows:
        d = dict(r)
        d["negative_themes"] = json.loads(d.get("negative_themes", "[]"))
        d["feature_requests"] = json.loads(d.get("feature_requests", "[]"))
        d["pain_points"] = json.loads(d.get("pain_points", "[]"))
        results.append(d)
    return results


def get_new_since(since: str) -> list[dict]:
    """Get opportunities first seen after a given ISO date."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM opportunities WHERE first_seen > ? ORDER BY disruption_score DESC",
        (since,)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_trending(limit: int = 20) -> list[dict]:
    """Get opportunities with the biggest positive score trend."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM opportunities WHERE score_trend > 0 ORDER BY score_trend DESC LIMIT ?",
        (limit,)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_score_history(opportunity_id: int, days: int = 30) -> list[dict]:
    """Get score history for an opportunity."""
    db = get_db()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = db.execute(
        "SELECT * FROM score_history WHERE opportunity_id=? AND recorded_at>? ORDER BY recorded_at",
        (opportunity_id, since)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    """Get aggregate stats."""
    db = get_db()
    stats = {
        "total": db.execute("SELECT COUNT(*) as c FROM opportunities").fetchone()["c"],
        "high_score": db.execute("SELECT COUNT(*) as c FROM opportunities WHERE disruption_score >= 40").fetchone()["c"],
        "sources": db.execute("SELECT DISTINCT source FROM opportunities").fetchall(),
        "categories": db.execute("SELECT DISTINCT category FROM opportunities WHERE category != ''").fetchall(),
        "runs": db.execute("SELECT COUNT(*) as c FROM scan_runs").fetchone()["c"],
        "last_run": db.execute("SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT 1").fetchone(),
        "avg_score": db.execute("SELECT AVG(disruption_score) as a FROM opportunities").fetchone()["a"] or 0,
    }
    stats["sources"] = [r["source"] for r in stats["sources"]]
    stats["categories"] = [r["category"] for r in stats["categories"]]
    if stats["last_run"]:
        stats["last_run"] = dict(stats["last_run"])
    db.close()
    return stats


def cleanup_old_data(days: int = None):
    """Remove old history data."""
    days = days or config.HISTORY_DAYS
    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db.execute("DELETE FROM score_history WHERE recorded_at < ?", (cutoff,))
    db.execute("DELETE FROM scan_runs WHERE started_at < ?", (cutoff,))
    db.commit()
    db.close()
