"""
Email alerts for the trading system.
====================================

Sends notifications via SMTP (Gmail or any provider).  Uses a small
on-disk dedupe log so the same alert key won't fire more often than once
per `ALERT_DEDUPE_MINUTES` (default 60).  Without this we'd spam the
inbox every 5 minutes while a monitor cycle is stuck.

Required environment variables (set in /root/polymarket/.env):
  SMTP_HOST          e.g. smtp.gmail.com
  SMTP_PORT          e.g. 587
  SMTP_USER          gmail address used to send
  SMTP_PASS          app password (NOT your gmail login password)
  ALERT_TO_EMAIL     where alerts go
  ALERT_FROM_NAME    (optional) human label, default "Polymarket Bot"
  ALERT_DEDUPE_MINUTES  (optional) minutes between repeats of the same key

Alerts are best-effort.  We never crash the calling process on email
failure — we only log and move on.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import socket
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger("notifier")

DEDUPE_FILE = Path("/root/polymarket/state/alert_dedupe.json")
DEFAULT_DEDUPE_MINUTES = 60


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _load_dedupe() -> dict:
    try:
        if DEDUPE_FILE.exists():
            return json.loads(DEDUPE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_dedupe(data: dict) -> None:
    try:
        DEDUPE_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEDUPE_FILE.write_text(json.dumps(data))
    except Exception as e:
        log.warning(f"dedupe save failed: {e}")


def _should_send(alert_key: str | None, dedupe_minutes: int) -> bool:
    """True if this alert key hasn't fired in the last `dedupe_minutes`."""
    if not alert_key:
        return True
    data = _load_dedupe()
    last = data.get(alert_key)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
        if _now_utc() - last_dt < timedelta(minutes=dedupe_minutes):
            return False
    except Exception:
        return True
    return True


def _mark_sent(alert_key: str | None) -> None:
    if not alert_key:
        return
    data = _load_dedupe()
    data[alert_key] = _now_utc().isoformat()
    # GC entries older than 7 days so the file doesn't grow forever
    cutoff = _now_utc() - timedelta(days=7)
    data = {
        k: v for k, v in data.items()
        if _parse_dt_safe(v) >= cutoff
    }
    _save_dedupe(data)


def _parse_dt_safe(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return _now_utc()


def send_alert(
    subject: str,
    body: str,
    *,
    severity: str = "warning",       # "info" | "warning" | "critical"
    alert_key: str | None = None,    # used for dedupe
    dedupe_minutes: int | None = None,
) -> bool:
    """
    Send an email alert.  Returns True if actually sent, False if
    suppressed or failed.  Never raises.
    """
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    pw   = os.getenv("SMTP_PASS", "")
    to   = os.getenv("ALERT_TO_EMAIL", "")
    from_name = os.getenv("ALERT_FROM_NAME", "Polymarket Bot")
    dedupe_min = dedupe_minutes if dedupe_minutes is not None else int(
        os.getenv("ALERT_DEDUPE_MINUTES", str(DEFAULT_DEDUPE_MINUTES))
    )

    if not (host and user and pw and to):
        log.warning(f"alert suppressed (SMTP env not configured): {subject}")
        return False

    if not _should_send(alert_key, dedupe_min):
        log.info(f"alert deduped: {alert_key} ({subject})")
        return False

    sev_tag = {
        "info":     "ℹ️ ",
        "warning":  "⚠️ ",
        "critical": "🚨 ",
    }.get(severity, "")
    hostname = socket.gethostname()

    msg = EmailMessage()
    msg["From"]    = f"{from_name} <{user}>"
    msg["To"]      = to
    msg["Subject"] = f"{sev_tag}[Polymarket] {subject}"
    msg.set_content(
        f"{body}\n\n"
        f"---\n"
        f"Severity: {severity}\n"
        f"Host:     {hostname}\n"
        f"Time:     {_now_utc().isoformat()}\n"
        f"Key:      {alert_key or '(none)'}\n"
    )

    try:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls()
            s.login(user, pw)
            s.send_message(msg)
        _mark_sent(alert_key)
        log.info(f"alert sent: {subject}")
        return True
    except Exception as e:
        log.error(f"alert send failed for '{subject}': {e}")
        return False
