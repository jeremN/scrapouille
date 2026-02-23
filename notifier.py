"""
🔔 Notifier — Send scan results via Discord, Slack, Email, or Ntfy.
"""

import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

import requests
import config


def notify_all(new_count: int, updated_count: int, total: int,
               top_apps: list[dict], new_apps: list[dict]):
    """Send notifications through all configured channels."""
    summary = _build_summary(new_count, updated_count, total, top_apps, new_apps)

    if config.DISCORD_WEBHOOK_URL:
        try:
            _send_discord(summary, top_apps, new_apps)
            print("  ✅ Discord notification sent")
        except Exception as e:
            print(f"  ❌ Discord error: {e}")

    if config.SLACK_WEBHOOK_URL:
        try:
            _send_slack(summary, top_apps, new_apps)
            print("  ✅ Slack notification sent")
        except Exception as e:
            print(f"  ❌ Slack error: {e}")

    if config.SMTP_HOST and config.EMAIL_TO:
        try:
            _send_email(summary, top_apps, new_apps)
            print("  ✅ Email notification sent")
        except Exception as e:
            print(f"  ❌ Email error: {e}")

    if config.NTFY_TOPIC:
        try:
            _send_ntfy(summary, new_count)
            print("  ✅ Ntfy notification sent")
        except Exception as e:
            print(f"  ❌ Ntfy error: {e}")


def _build_summary(new_count: int, updated_count: int, total: int,
                   top_apps: list[dict], new_apps: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"🎯 Disruption Scanner — {now}",
        f"📊 {total} opportunities tracked | {new_count} new | {updated_count} updated",
        "",
    ]

    if new_apps:
        lines.append(f"🆕 New opportunities ({len(new_apps)}):")
        for app in new_apps[:5]:
            score = app.get("disruption_score", 0)
            name = app.get("name", "?")
            source = app.get("source", "?")
            lines.append(f"  • {name} ({source}) — Score: {score:.0f}/100")
        if len(new_apps) > 5:
            lines.append(f"  ... and {len(new_apps) - 5} more")
        lines.append("")

    lines.append("🔥 Top 5 opportunities:")
    for app in top_apps[:5]:
        score = app.get("disruption_score", 0)
        name = app.get("name", "?")
        rating = app.get("rating", 0)
        reviews = app.get("num_reviews", 0)
        trend = app.get("score_trend", 0)
        trend_str = f" (↑{trend:+.0f})" if trend > 0 else f" ({trend:+.0f})" if trend < 0 else ""
        lines.append(f"  {'🔥' if score >= 40 else '⚡' if score >= 20 else '📊'} {name}: {score:.0f}/100{trend_str}")
        if rating:
            lines.append(f"    ⭐ {rating:.1f} | 📝 {reviews} reviews")

    return "\n".join(lines)


# ─── Discord ──────────────────────────────────────────────────────────────────

def _send_discord(summary: str, top_apps: list[dict], new_apps: list[dict]):
    """Send a rich Discord embed via webhook."""
    embeds = []

    # Main summary embed
    embed = {
        "title": "🎯 Disruption Scanner — Daily Report",
        "description": summary,
        "color": 0xFF6B6B,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Disruption Scanner"},
    }

    # Add fields for top opportunities
    if top_apps:
        fields = []
        for app in top_apps[:5]:
            score = app.get("disruption_score", 0)
            name = app.get("name", "?")
            url = app.get("url", "")
            themes = json.loads(app.get("negative_themes", "[]")) if isinstance(app.get("negative_themes"), str) else app.get("negative_themes", [])
            value = f"Score: **{score:.0f}/100**"
            if app.get("rating"):
                value += f" | ⭐ {app['rating']:.1f}"
            if themes:
                value += f"\nPain: {', '.join(themes[:3])}"
            if url:
                value += f"\n[→ View]({url})"

            fields.append({
                "name": f"{'🔥' if score >= 40 else '⚡'} {name}",
                "value": value,
                "inline": True,
            })

        embed["fields"] = fields

    embeds.append(embed)

    # New opportunities embed
    if new_apps:
        new_embed = {
            "title": f"🆕 {len(new_apps)} New Opportunities",
            "color": 0x6BCB77,
            "fields": [],
        }
        for app in new_apps[:8]:
            new_embed["fields"].append({
                "name": app.get("name", "?"),
                "value": f"Score: {app.get('disruption_score', 0):.0f} | {app.get('source', '?')}",
                "inline": True,
            })
        embeds.append(new_embed)

    payload = {
        "username": "Disruption Scanner",
        "embeds": embeds[:2],
    }

    resp = requests.post(config.DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    resp.raise_for_status()


# ─── Slack ────────────────────────────────────────────────────────────────────

def _send_slack(summary: str, top_apps: list[dict], new_apps: list[dict]):
    """Send a Slack message via webhook."""
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🎯 Disruption Scanner — Daily Report"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": summary}
        },
        {"type": "divider"},
    ]

    # Top apps section
    for app in top_apps[:5]:
        score = app.get("disruption_score", 0)
        name = app.get("name", "?")
        url = app.get("url", "#")
        emoji = "🔥" if score >= 40 else "⚡" if score >= 20 else "📊"
        text = f"{emoji} *<{url}|{name}>* — Score: *{score:.0f}/100*"
        if app.get("rating"):
            text += f" | ⭐ {app['rating']:.1f}"
        themes = app.get("negative_themes", [])
        if isinstance(themes, str):
            themes = json.loads(themes)
        if themes:
            text += f"\n> Pain: _{', '.join(themes[:3])}_"

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": text}
        })

    payload = {"blocks": blocks}
    resp = requests.post(config.SLACK_WEBHOOK_URL, json=payload, timeout=10)
    resp.raise_for_status()


# ─── Email ────────────────────────────────────────────────────────────────────

def _send_email(summary: str, top_apps: list[dict], new_apps: list[dict]):
    """Send an HTML email with the report."""
    recipients = [e.strip() for e in config.EMAIL_TO.split(",") if e.strip()]
    if not recipients:
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎯 Disruption Scanner — {datetime.now().strftime('%Y-%m-%d')}"
    msg["From"] = config.EMAIL_FROM
    msg["To"] = ", ".join(recipients)

    # Plain text
    msg.attach(MIMEText(summary, "plain"))

    # HTML version
    html_rows = ""
    for app in top_apps[:10]:
        score = app.get("disruption_score", 0)
        name = app.get("name", "?")
        url = app.get("url", "#")
        rating = app.get("rating", 0)
        reviews = app.get("num_reviews", 0)
        color = "#ff6b6b" if score >= 40 else "#ffd93d" if score >= 20 else "#6bcb77"
        themes = app.get("negative_themes", [])
        if isinstance(themes, str):
            themes = json.loads(themes)

        html_rows += f"""
        <tr>
            <td><a href="{url}" style="color:#4d96ff;text-decoration:none;font-weight:600">{name}</a></td>
            <td style="color:{color};font-weight:700;text-align:center">{score:.0f}</td>
            <td style="text-align:center">{"⭐ " + f"{rating:.1f}" if rating else "—"}</td>
            <td style="text-align:center">{reviews or "—"}</td>
            <td style="font-size:12px;color:#666">{", ".join(themes[:2]) if themes else "—"}</td>
        </tr>"""

    html = f"""
    <html><body style="font-family:-apple-system,sans-serif;background:#f5f5f5;padding:20px">
    <div style="max-width:700px;margin:0 auto;background:white;border-radius:12px;padding:30px;box-shadow:0 2px 8px rgba(0,0,0,0.1)">
        <h1 style="color:#333;font-size:22px">🎯 Disruption Scanner Report</h1>
        <p style="color:#666">{datetime.now().strftime("%B %d, %Y")}</p>
        <div style="display:flex;gap:20px;margin:20px 0">
            <div style="background:#fff0f0;padding:15px;border-radius:8px;text-align:center;flex:1">
                <div style="font-size:28px;font-weight:700;color:#ff6b6b">{len(new_apps)}</div>
                <div style="font-size:12px;color:#666">New</div>
            </div>
            <div style="background:#f0f8ff;padding:15px;border-radius:8px;text-align:center;flex:1">
                <div style="font-size:28px;font-weight:700;color:#4d96ff">{len(top_apps)}</div>
                <div style="font-size:12px;color:#666">Tracked</div>
            </div>
        </div>
        <table style="width:100%;border-collapse:collapse;margin-top:20px">
            <tr style="background:#f9f9f9">
                <th style="text-align:left;padding:8px;font-size:13px">App</th>
                <th style="padding:8px;font-size:13px">Score</th>
                <th style="padding:8px;font-size:13px">Rating</th>
                <th style="padding:8px;font-size:13px">Reviews</th>
                <th style="padding:8px;font-size:13px;text-align:left">Pain Points</th>
            </tr>
            {html_rows}
        </table>
    </div>
    </body></html>"""

    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.starttls()
        if config.SMTP_USER:
            server.login(config.SMTP_USER, config.SMTP_PASS)
        server.sendmail(config.EMAIL_FROM, recipients, msg.as_string())


# ─── Ntfy (simple push) ──────────────────────────────────────────────────────

def _send_ntfy(summary: str, new_count: int):
    """Send a push notification via ntfy.sh."""
    url = f"{config.NTFY_SERVER}/{config.NTFY_TOPIC}"
    title = f"🎯 Disruption Scanner — {new_count} new"
    # Truncate for push notification
    body = summary[:500]

    requests.post(url, data=body.encode("utf-8"), headers={
        "Title": title,
        "Priority": "default" if new_count == 0 else "high",
        "Tags": "chart_with_upwards_trend",
    }, timeout=10)
