"""Cron/data healthcheck: is the platform actually current?

check() inspects the run tables and data freshness; notify() pushes
problems to ntfy.sh so a dead cron shows up on the phone instead of in a
log nobody reads. Wired into the crontab after the morning and evening
job windows (scripts/healthcheck.py).
"""
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from . import db, ntfy

# A run row whose ok is still NULL after this long crashed mid-way.
STUCK_AFTER_HOURS = 3

# Weekly backup (Sat 08:00) counts as stale after this many days.
BACKUP_MAX_AGE_DAYS = 8
BACKUP_FILE = Path(os.environ.get("BACKUP_DIR", "/desktop")) / "bondlens.dump"


# Longest plausible exchange closure between two trading days (a holiday
# cluster around a weekend). More consecutive file-less weekdays than
# this is a publication failure, not a holiday, and must alert.
MAX_INFERRED_HOLIDAYS = 3
EOD_SOURCES = 3  # nse_cm, nse_cbm, bse_debt


def expected_trade_date(today: date, holidays: set[date]) -> tuple[date, list[date]]:
    """Latest date EOD trades must exist for, walking back over weekends
    and over weekdays in `holidays`, at most MAX_INFERRED_HOLIDAYS of them.
    Returns (expected, holidays skipped). Pure, so it is unit-testable."""
    d = today - timedelta(days=1)
    skipped: list[date] = []
    while True:
        if d.weekday() >= 5:
            d -= timedelta(days=1)
        elif d in holidays and len(skipped) < MAX_INFERRED_HOLIDAYS:
            skipped.append(d)
            d -= timedelta(days=1)
        else:
            return d, skipped


def inferred_holidays(conn, since: date) -> set[date]:
    """Weekdays on which every exchange source answered 'no file' to a
    harvest that ran after that day's evening publication window. All
    three exchanges silent on the same weekday is the holiday signature
    (14 Sept 2026, Ganesh Chaturthi, paged as 'eod: latest trades
    2026-09-11 (expected >= 2026-09-14)' before this existed)."""
    rows = conn.execute(
        "SELECT r.trade_date FROM eod_raw r "
        "WHERE r.trade_date >= %s "
        "  AND extract(isodow FROM r.trade_date) <= 5 "
        "GROUP BY r.trade_date "
        "HAVING count(*) = %s "
        "   AND count(*) FILTER (WHERE r.http_status = 200) = 0 "
        "   AND max(r.fetched_at) >= "
        "       (r.trade_date::timestamp + interval '19 hours') "
        "       AT TIME ZONE 'Asia/Kolkata'",
        (since, EOD_SOURCES)).fetchall()
    return {r["trade_date"] for r in rows}


def nsdl_problem(runs: list, now: datetime) -> str | None:
    """Human-readable NSDL verdict from today's run rows (oldest first), or
    None when the latest run is fine. Pure, so it is unit-testable."""
    if not runs:
        return "nsdl: no run today"
    latest = runs[-1]
    if latest["ok"] is not False:
        return None
    attempts = "morning and midday runs" if len(runs) > 1 else "today's run"
    detail = latest["notes"] or "no detail recorded"
    hint = ""
    if "NsdlOutage" in detail or "consecutive upstream" in detail:
        hint = " NSDL upstream down (aborted early)."
    elif "canary ok" in detail:
        hint = " NSDL answered the canary; failures are per-record, not an outage."
    elif (latest["fetched"] or 0) == 0 and (latest["failed"] or 0) > 0:
        hint = " Fetched nothing; NSDL upstream likely down."
    ist_hour = (now + timedelta(hours=5, minutes=30)).hour
    retry = " Auto-retry at 13:00 IST." if len(runs) == 1 and ist_hour < 13 else ""
    return f"nsdl: {attempts} FAILED \u2014 {detail}.{hint}{retry}"


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

        # 2) NSDL harvest ran today and succeeded (daily 07:00 IST, with a
        #    conditional 13:00 retry). Say *why* it failed so the alert is
        #    actionable without opening the logs.
        rows = conn.execute(
            "SELECT ok, fetched, failed, notes, "
            "       (run_at AT TIME ZONE 'Asia/Kolkata')::time AS at "
            "FROM nsdl_runs WHERE run_at::date = %s ORDER BY id", (today,)).fetchall()
        problem = nsdl_problem(rows, now)
        if problem:
            problems.append(problem)

        # 3) EOD trades no older than the last trading day, where a
        #    weekday every exchange left file-less counts as a holiday.
        r = conn.execute(
            "SELECT max(trade_date) AS d FROM eod_trades").fetchone()
        holidays = inferred_holidays(conn, today - timedelta(days=14))
        expected, skipped = expected_trade_date(today, holidays)
        if r is None or r["d"] is None:
            problems.append("eod: no trades at all")
        elif r["d"] < expected:
            why = (f"; {', '.join(map(str, skipped))} had no files on any "
                   "exchange - holiday") if skipped else ""
            problems.append(
                f"eod: latest trades {r['d']} (expected >= {expected}{why})")

        # A recent trade from one source can hide a failed harvest or parser
        # for another source, so also inspect the run and raw-file state.
        r = conn.execute(
            "SELECT ok FROM eod_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if r is None:
            problems.append("eod: no harvest runs")
        elif r["ok"] is False:
            problems.append("eod: latest harvest FAILED")

        r = conn.execute(
            "SELECT count(*) AS n FROM eod_raw "
            "WHERE http_status = 200 AND content IS NOT NULL "
            "AND parsed_at IS NULL"
        ).fetchone()
        if r and r["n"]:
            problems.append(f"eod: {r['n']} fetched file(s) awaiting parse")

        # 4) Explorer analytics recomputed within ~a day.
        r = conn.execute(
            "SELECT max(computed_at) AS t FROM bond_analytics").fetchone()
        if r is None or r["t"] is None:
            problems.append("analytics: bond_analytics is empty")
        elif now - r["t"] > timedelta(hours=26):
            problems.append(f"analytics: stale (computed {r['t']:%Y-%m-%d %H:%M})")

        # 5) Crashed runs: ok still NULL long after start.
        cutoff = now - timedelta(hours=STUCK_AFTER_HOURS)
        for table in ("bond_runs", "nsdl_runs", "eod_runs"):
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
    """Push to the ops ntfy topic; False (with a printed warning) if unset."""
    return ntfy.notify_ops(title, message, priority)
