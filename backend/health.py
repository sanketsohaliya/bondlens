"""Cron/data healthcheck: is the platform actually current?

check() inspects the run tables and data freshness; notify() pushes
problems to ntfy.sh so a dead cron shows up on the phone instead of in a
log nobody reads. Wired into the crontab after the morning and evening
job windows (scripts/healthcheck.py).
"""
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests

from . import db

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh")

# A run row whose ok is still NULL after this long crashed mid-way.
STUCK_AFTER_HOURS = 3

# Weekly backup (Sat 08:00) counts as stale after this many days.
BACKUP_MAX_AGE_DAYS = 8
BACKUP_FILE = Path(os.environ.get("BACKUP_DIR", "/desktop")) / "bondlens.dump"


def _last_trading_day(today: date) -> date:
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def check(now: datetime | None = None) -> list[str]:
    """Return a list of human-readable problems (empty = healthy)."""
    now = now or datetime.now(UTC)
    today = now.date()
    problems: list[str] = []

    with db._conn() as conn:
        # 1) Universe build ran today and succeeded (Mon-Sat 05:30 IST).
        if today.weekday() != 6:  # no run scheduled on Sundays
            r = conn.execute(
                "SELECT ok FROM bond_runs WHERE run_at::date = %s "
                "ORDER BY id DESC LIMIT 1", (today,)).fetchone()
            if r is None:
                problems.append("universe: no run today")
            elif r["ok"] is False:
                problems.append("universe: today's run FAILED")

        # 2) NSDL harvest ran today and succeeded (daily 07:00 IST).
        r = conn.execute(
            "SELECT ok FROM nsdl_runs WHERE run_at::date = %s "
            "ORDER BY id DESC LIMIT 1", (today,)).fetchone()
        if r is None:
            problems.append("nsdl: no run today")
        elif r["ok"] is False:
            problems.append("nsdl: today's run FAILED")

        # 3) EOD trades no older than the last trading day.
        r = conn.execute(
            "SELECT max(trade_date) AS d FROM eod_trades").fetchone()
        expected = _last_trading_day(today)
        if r is None or r["d"] is None:
            problems.append("eod: no trades at all")
        elif r["d"] < expected:
            problems.append(
                f"eod: latest trades {r['d']} (expected >= {expected})")

        # 4) Explorer analytics recomputed within ~a day.
        r = conn.execute(
            "SELECT max(computed_at) AS t FROM bond_analytics").fetchone()
        if r is None or r["t"] is None:
            problems.append("analytics: bond_analytics is empty")
        elif now - r["t"] > timedelta(hours=26):
            problems.append(f"analytics: stale (computed {r['t']:%Y-%m-%d %H:%M})")

        # 5) Crashed runs: ok still NULL long after start.
        cutoff = now - timedelta(hours=STUCK_AFTER_HOURS)
        for table in ("bond_runs", "nsdl_runs"):
            r = conn.execute(
                f"SELECT count(*) AS n FROM {table} "  # noqa: S608 - literal table
                "WHERE ok IS NULL AND run_at < %s", (cutoff,)).fetchone()
            if r and r["n"]:
                problems.append(f"{table}: {r['n']} run(s) stuck mid-way")

    # 6) Weekly backup landed recently (only where /desktop is mounted).
    if BACKUP_FILE.parent.exists():
        if not BACKUP_FILE.exists():
            problems.append(f"backup: {BACKUP_FILE} missing")
        else:
            age = now - datetime.fromtimestamp(
                BACKUP_FILE.stat().st_mtime, tz=UTC)
            if age > timedelta(days=BACKUP_MAX_AGE_DAYS):
                problems.append(f"backup: {age.days} days old")

    with db._conn() as conn:
        available = conn.execute("SELECT to_regclass('corporate_action_runs') AS name").fetchone()
    if available and available["name"]:
        from .corporate_action_store import Store
        from .corporate_actions import capture_health

        problems.extend(capture_health(Store().snapshot(), now))

    return problems


def notify(title: str, message: str, priority: str = "high") -> bool:
    """Push to ntfy.sh; returns False (with a printed warning) if unset."""
    if not NTFY_TOPIC:
        print(f"NTFY_TOPIC unset — would have sent: [{title}] {message}")
        return False
    resp = requests.post(
        f"{NTFY_URL}/{NTFY_TOPIC}",
        data=message.encode(),
        headers={"Title": title, "Priority": priority, "Tags": "warning,bondlens"},
        timeout=15,
    )
    resp.raise_for_status()
    return True
