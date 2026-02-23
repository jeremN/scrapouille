"""
⚙️ Configuration — loaded from environment variables / .env file.
"""

import os
from pathlib import Path

# Try to load .env
ENV_PATH = Path(__file__).parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# ─── Schedule ─────────────────────────────────────────────────────────────────
SCHEDULE_CRON = os.getenv("SCHEDULE_CRON", "0 8 * * *")  # Default: every day at 8am
TIMEZONE = os.getenv("TIMEZONE", "Europe/Paris")

# ─── Scraper settings ────────────────────────────────────────────────────────
SCRAPER_SOURCES = os.getenv("SCRAPER_SOURCES", "reddit,hn,producthunt,indiehackers,exploding").split(",")
SCANNER_SOURCES = os.getenv("SCANNER_SOURCES", "g2,alternativeto,github,reddit").split(",")
MAX_RATING = float(os.getenv("MAX_RATING", "4.0"))
MIN_REVIEWS = int(os.getenv("MIN_REVIEWS", "20"))
LIMIT_PER_SOURCE = int(os.getenv("LIMIT_PER_SOURCE", "30"))
SEARCH_KEYWORDS = os.getenv("SEARCH_KEYWORDS", "")  # comma-separated

# ─── Storage ──────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DB_PATH = DATA_DIR / "disruption.db"
REPORTS_DIR = DATA_DIR / "reports"
HISTORY_DAYS = int(os.getenv("HISTORY_DAYS", "90"))  # Keep reports for N days

# ─── Notifications ────────────────────────────────────────────────────────────
# Discord
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# Slack
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

# Email (SMTP)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")  # comma-separated

# Ntfy (simple push notifications)
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh")

# ─── Dashboard ────────────────────────────────────────────────────────────────
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")

# ─── Ensure dirs exist ───────────────────────────────────────────────────────
DATA_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
