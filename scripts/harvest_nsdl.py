"""Harvest every per-ISIN NSDL bdsinfo endpoint for the v2 bond universe.

Raw-first: responses are stored verbatim as JSONB in nsdl_raw,
append-only, so parsing decisions can change later without refetching and
the history of changing data (ratings, holdings, redemptions) is retained.

Endpoint tiers (refresh policy):
  static    isins, listings, keycontacts
            -- issuer/listing/contact metadata; fetched once on success
  weekly    instruments, coupondetail, redemptions, credit_ratings,
            defaultdetail, getTDbyIsin, restructuring -- financial terms
  monthly   keydocuments
            -- slow-moving documents
  quarterly quarterenddate + publicshareholderdetails
            -- investor category holdings; ALL quarters are fetched
               (historical), already-captured quarters are frozen

A 400 "No Record Found" is a meaningful terminal answer (clean bond) and
is stored like a success. Identical re-fetches only bump last_checked_at
instead of inserting duplicate payload rows. A per-record 5xx (NSDL has
~16 ISIN/endpoint pairs that always answer 503 while everything around
them works) is stored too and retried weekly, so it neither poisons the
parse (which reads only 200 rows) nor gets hammered every run.

Usage:
  .venv/bin/python scripts/harvest_nsdl.py                # all due work
  .venv/bin/python scripts/harvest_nsdl.py --tier static  # one tier only
  .venv/bin/python scripts/harvest_nsdl.py --isin INE238A08419
  .venv/bin/python scripts/harvest_nsdl.py --workers 6 --limit 100
"""
import argparse
import hashlib
import json
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

import requests

sys.path.insert(0, ".")
from backend import db  # noqa: E402

BASE = "https://www.indiabondinfo.nsdl.com/bds-service/v1/public/bdsinfo"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json",
    "Referer": "https://www.indiabondinfo.nsdl.com/CBDServices/",
}

# endpoint -> (path, tier); ordered to match the NSDL UI sidebar:
# Issuer, Instrument, Coupon, Redemption, Credit Rating, Listing,
# Restructuring, Default, Investor Category Holdings, Key Contacts,
# Key Documents. (Liquidity Window Facility has no public API.)
ENDPOINTS = {
    "isins": ("/isins", "static"),          # actually /public/isins, see fetch()
    "instruments": ("/instruments", "weekly"),
    "coupondetail": ("/coupondetail", "weekly"),
    "redemptions": ("/redemptions", "weekly"),
    "credit_ratings": ("/credit-ratings", "weekly"),
    "listings": ("/listings", "static"),
    "restructuring": ("/restructuring", "weekly"),
    "defaultdetail": ("/defaultdetail", "weekly"),
    "technical_default": ("/getTDbyIsin", "weekly"),
    "quarterenddate": ("/quarterenddate", "quarterly"),
    "keycontacts": ("/keycontacts", "static"),
    "keydocuments": ("/keydocuments", "monthly"),
}
HOLDINGS_EP = "publicshareholderdetails"   # tier: quarterly, keyed by quarter

MAX_AGE = {"weekly": timedelta(days=7), "monthly": timedelta(days=30),
           "quarterly": timedelta(days=90)}
_MONTHS = {"Jan": "January", "Feb": "February", "Mar": "March",
           "Apr": "April", "May": "May", "Jun": "June", "Jul": "July",
           "Aug": "August", "Sep": "September", "Oct": "October",
           "Nov": "November", "Dec": "December"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS nsdl_runs (
    id BIGSERIAL PRIMARY KEY,
    run_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    tiers TEXT,
    workers INTEGER,
    isins INTEGER,
    fetched INTEGER,
    unchanged INTEGER,
    skipped INTEGER,
    failed INTEGER,
    ok BOOLEAN,
    notes TEXT
);
ALTER TABLE nsdl_runs ADD COLUMN IF NOT EXISTS broken INTEGER;
CREATE TABLE IF NOT EXISTS nsdl_raw (
    id BIGSERIAL PRIMARY KEY,
    isin TEXT NOT NULL REFERENCES bonds (isin),
    endpoint TEXT NOT NULL,
    quarter TEXT NOT NULL DEFAULT '',
    run_id BIGINT REFERENCES nsdl_runs (id),
    fetched_at TIMESTAMPTZ NOT NULL,
    last_checked_at TIMESTAMPTZ NOT NULL,
    http_status INTEGER NOT NULL,
    sha256 TEXT,
    payload JSONB
);
ALTER TABLE nsdl_raw
    ADD COLUMN IF NOT EXISTS run_id BIGINT REFERENCES nsdl_runs (id);
CREATE INDEX IF NOT EXISTS idx_nsdl_raw_key
    ON nsdl_raw (isin, endpoint, quarter, fetched_at DESC);
"""

DELAY = 0.3            # polite gap between calls within one worker (s)
MAX_ATTEMPTS = 4

_local = threading.local()
_db_lock = threading.Lock()
_stats = {"fetched": 0, "unchanged": 0, "skipped": 0, "failed": 0, "broken": 0}
_stats_lock = threading.Lock()

# Fail fast on a dead upstream. On 13 Sept 2026 NSDL answered 503 to all
# 192 calls for four minutes while the harvester patiently retried each
# one; twelve consecutive upstream failures (5xx or connection error) with
# no success anywhere in the pool *looks like* an outage. But on 14/15
# Sept the only due work was the ~16 chronically broken records, which
# also produced a flat run of 503s - so before declaring an outage the
# streak is checked against a canary request to a known-good endpoint.
# A 4xx is NSDL *answering* and does not count.
OUTAGE_STREAK = 12
_outage = {"streak": 0, "last": None, "canary_ok": 0, "dead": False}
_status_counts: dict[int, int] = {}

# Axis Bank Basel-III tier-II (in the universe, answers 200 today). If it
# ever disappears upstream_alive() falls back to the first universe ISIN.
CANARY_ISIN = "INE238A08419"


class NsdlOutage(RuntimeError):
    """Raised from fetch() once the upstream looks systemically down."""


def reset_outage_tracking():
    with _stats_lock:
        _outage.update(streak=0, last=None, canary_ok=0, dead=False)
        _status_counts.clear()


def upstream_alive(isin: str | None = None) -> bool:
    """One fresh GET against a record NSDL is known to serve. True iff it
    comes back 200 with a JSON body: NSDL is answering, so a flat run of
    503s elsewhere is per-record breakage rather than an outage."""
    isin = isin or CANARY_ISIN
    try:
        r = session(rotate=True).get(f"{BASE}/instruments?isin={isin}",
                                     timeout=45)
        if r.status_code == 200 and isinstance(json.loads(r.text), (dict, list)):
            return True
        if isin == CANARY_ISIN and r.status_code < 500:
            fallback = next(iter(db.universe_isins()), None)
            return bool(fallback) and fallback != isin and upstream_alive(fallback)
    except Exception:  # noqa: BLE001 - any failure reads as "not alive"
        pass
    return False


def _note_upstream_failure(status):
    """Count a 5xx/connection failure. Once the streak says outage, ask
    the canary: alive -> reset and carry on; dead -> raise NsdlOutage."""
    with _stats_lock:
        if status is not None:
            _status_counts[status] = _status_counts.get(status, 0) + 1
        _outage["streak"] += 1
        _outage["last"] = status
        streak, last, dead = _outage["streak"], _outage["last"], _outage["dead"]
    if streak < OUTAGE_STREAK:
        return
    what = f"http={last}" if last is not None else "connection errors"
    if dead:  # already decided; other workers must not re-probe
        raise NsdlOutage(f"{streak} consecutive upstream failures ({what})")
    if upstream_alive():
        with _stats_lock:
            _outage["streak"] = 0
            _outage["canary_ok"] += 1
        print(f"  canary ok after {streak} upstream failures; not an outage",
              flush=True)
        return
    with _stats_lock:
        _outage["dead"] = True
    raise NsdlOutage(f"{streak} consecutive upstream failures ({what})")


def _note_upstream_success():
    with _stats_lock:
        _outage["streak"] = 0


def status_counts() -> dict[int, int]:
    with _stats_lock:
        return dict(_status_counts)


def failure_note(failed: int, broken: int = 0,
                 canary_ok: bool | None = None) -> str | None:
    """'48 fetch failures (503x192)' - what the healthcheck relays.
    Broken records and a passed canary are named so a light day full of
    per-record 503s does not read as an outage."""
    if not failed and not broken:
        return None
    counts = status_counts()
    detail = ", ".join(f"{k}x{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
    parts = []
    if failed:
        parts.append(f"{failed} fetch failures" + (f" ({detail})" if detail else ""))
    if broken:
        parts.append(f"{broken} broken records" +
                     ("" if failed or not detail else f" ({detail})"))
    if canary_ok is None and _outage["canary_ok"]:
        canary_ok = True
    if canary_ok is True:
        parts.append("canary ok - per-record NSDL errors, not an outage")
    elif canary_ok is False:
        parts.append("canary failed - NSDL upstream down")
    return "; ".join(parts)


def run_ok(fetched: int, unchanged: int, failed: int,
           upstream_alive: bool | None = None) -> bool:
    """A few dead ISINs out of tens of thousands of calls is normal, so
    tolerate max(25, 1%) failures. A run that attempted work and got
    nothing back is only ok when the canary proved NSDL was answering
    (then the failures are per-record, e.g. the 16 chronic 503s being the
    only due work on 14/15 Sept 2026); without that proof it is an outage.
    Nothing due at all is legitimately fine."""
    attempts = fetched + unchanged + failed
    if attempts == 0:
        return True
    if fetched + unchanged == 0:
        return upstream_alive is True
    return failed <= max(25, attempts * 0.01)


def _bump(key, n=1):
    with _stats_lock:
        _stats[key] += n


def session(rotate: bool = False) -> requests.Session:
    """Thread-local session with NSDL's cookie handshake; NSDL's LB
    throttles connections that hammer one socket, so rotate on failure."""
    if rotate or getattr(_local, "session", None) is None:
        s = requests.Session()
        s.headers.update(HEADERS)
        try:
            s.get("https://www.indiabondinfo.nsdl.com/CBDServices/",
                  timeout=60)
        except Exception:  # noqa: BLE001 - handshake is best-effort
            pass
        _local.session = s
    return _local.session


def fetch(url: str, tag: str):
    """GET with retries. Returns (http_status, text) or (None, None).
    JSON success and explicit 400 'No Record Found' are cacheable; a
    record that answers 5xx on every attempt is returned as-is so the
    caller can store it and back off, rather than retrying it every run.
    Connection errors and unrelenting 429s stay (None, None)."""
    last_5xx = None
    for attempt in range(MAX_ATTEMPTS):
        delay = min(5 * (attempt + 1), 20)
        try:
            r = session(rotate=attempt > 0).get(url, timeout=45)
            if r.status_code in (200, 400):
                payload = json.loads(r.text)
                if r.status_code == 200 and isinstance(payload, (dict, list)):
                    _note_upstream_success()
                    return r.status_code, r.text
                if r.status_code == 400 and "no record found" in str(payload).lower():
                    _note_upstream_success()
                    return r.status_code, r.text
            if r.status_code < 500 and r.status_code != 429:
                # NSDL answered; the ISIN/endpoint is simply not there.
                _note_upstream_success()
                return None, None
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    delay = int(retry_after)
                    if delay > 60:
                        return None, None
            else:
                last_5xx = (r.status_code, r.text)
                _note_upstream_failure(r.status_code)
            print(f"  {tag}: http={r.status_code} retry", flush=True)
        except NsdlOutage:
            raise
        except Exception as exc:  # noqa: BLE001
            last_5xx = None
            _note_upstream_failure(None)
            print(f"  {tag}: {str(exc)[:60]} retry", flush=True)
        if attempt + 1 < MAX_ATTEMPTS:
            time.sleep(delay)
    return last_5xx or (None, None)


def load_state():
    """Latest row per (isin, endpoint, quarter) -> (status, sha, checked)."""
    with db._conn() as conn:
        conn.execute(SCHEMA)
        rows = conn.execute(
            "SELECT DISTINCT ON (isin, endpoint, quarter) "
            "  isin, endpoint, quarter, http_status, sha256, last_checked_at "
            "FROM nsdl_raw ORDER BY isin, endpoint, quarter, fetched_at DESC"
        ).fetchall()
    return {(r["isin"], r["endpoint"], r["quarter"]):
            (r["http_status"], r["sha256"], r["last_checked_at"])
            for r in rows}


def store(isin, endpoint, quarter, status, text, prev, run_id):
    """Insert a new row, or just bump last_checked_at when unchanged."""
    now = datetime.now(UTC)
    sha = hashlib.sha256(text.encode()).hexdigest()
    try:
        payload = json.loads(text)
    except ValueError:
        payload = {"_raw_text": text[:20000]}
    with _db_lock, db._conn() as conn:
        if prev and prev[0] == status and prev[1] == sha:
            conn.execute(
                "UPDATE nsdl_raw SET last_checked_at = %s "
                "WHERE isin = %s AND endpoint = %s AND quarter = %s "
                "  AND sha256 = %s",
                (now, isin, endpoint, quarter, sha))
            _bump("broken" if status >= 500 else "unchanged")
        else:
            conn.execute(
                "INSERT INTO nsdl_raw (isin, endpoint, quarter, run_id, "
                " fetched_at, last_checked_at, http_status, sha256, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (isin, endpoint, quarter, run_id, now, now, status, sha,
                 json.dumps(payload)))
            _bump("broken" if status >= 500 else "fetched")
    return status, sha, now


def latest_run_today():
    """Most recent nsdl_runs row started today (IST), or None."""
    with db._conn() as conn:
        return conn.execute(
            "SELECT id, ok FROM nsdl_runs "
            "WHERE (run_at AT TIME ZONE 'Asia/Kolkata')::date = "
            "      (now() AT TIME ZONE 'Asia/Kolkata')::date "
            "ORDER BY id DESC LIMIT 1").fetchone()


def should_skip_retry() -> bool:
    """--if-failed-today: only re-run when today's latest run is not ok.
    A missing run also proceeds (the 07:00 job should always exist; if it
    does not, something else went wrong and a harvest can only help)."""
    latest = latest_run_today()
    if latest and latest["ok"] is True:
        print(f"morning run {latest['id']} ok; skipping retry", flush=True)
        return True
    return False


def start_nsdl_run(tiers, workers, n_isins):
    """Open an nsdl_runs row; ok stays NULL until finalized, so a crashed
    run is queryable (same convention as bond_runs)."""
    with db._conn() as conn:
        row = conn.execute(
            "INSERT INTO nsdl_runs (run_at, tiers, workers, isins) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (datetime.now(UTC), ",".join(sorted(tiers)),
             workers, n_isins)).fetchone()
    assert row is not None
    return row["id"]


def finish_nsdl_run(run_id, ok, notes=None):
    with _db_lock, db._conn() as conn:
        conn.execute(
            "UPDATE nsdl_runs SET finished_at = %s, fetched = %s, "
            " unchanged = %s, skipped = %s, failed = %s, broken = %s, "
            " ok = %s, notes = %s WHERE id = %s",
            (datetime.now(UTC), _stats["fetched"],
             _stats["unchanged"], _stats["skipped"], _stats["failed"],
             _stats["broken"], ok, notes, run_id))


def due(prev, tier, force):
    """Should this (isin, endpoint) be fetched now? A stored 5xx is a
    record NSDL cannot serve today; retry it weekly whatever the tier."""
    if force or prev is None:
        return True
    if prev[0] >= 500:
        return prev[2] < datetime.now(UTC) - MAX_AGE["weekly"]
    if prev[0] not in (200, 400):
        return True
    if tier == "static":
        return False  # terminal answer already stored
    return prev[2] < datetime.now(UTC) - MAX_AGE[tier]


def harvest_holdings(isin, state, quarters_payload, force, run_id):
    """Investor category holdings: one call per quarter, ALL quarters
    (history); a past quarter with a stored 200 payload is frozen."""
    for q in quarters_payload:
        raw = (q.get("quarter") or "").strip()
        mon, _, yr = raw.partition(" ")
        if mon not in _MONTHS or not yr:
            continue
        quarter = f"{_MONTHS[mon]} {yr}"
        prev = state.get((isin, HOLDINGS_EP, quarter))
        if prev and prev[0] == 200 and not force:
            _bump("skipped")
            continue
        url = (f"{BASE}/isin/{isin}/quarterdate/"
               f"{urllib.parse.quote(quarter)}/{HOLDINGS_EP}")
        status, text = fetch(url, f"{isin}/holdings/{quarter}")
        if status is None:
            _bump("failed")
            continue
        store(isin, HOLDINGS_EP, quarter, status, text, prev, run_id)
        time.sleep(DELAY)


def harvest_isin(isin, state, tiers, force, run_id):
    for name, (path, tier) in ENDPOINTS.items():
        if tier not in tiers:
            continue
        prev = state.get((isin, name, ""))
        if not due(prev, tier, force):
            _bump("skipped")
            continue
        if name == "isins":
            url = f"{BASE.rsplit('/bdsinfo', 1)[0]}/isins?isin={isin}"
        else:
            url = f"{BASE}{path}?isin={isin}"
        status, text = fetch(url, f"{isin}/{name}")
        if status is None:
            _bump("failed")
            continue
        store(isin, name, "", status, text, prev, run_id)
        time.sleep(DELAY)
        # holdings immediately after quarterenddate, matching the UI's
        # Investor Category Holdings position in the sidebar.
        if name == "quarterenddate" and status == 200 and text is not None:
            try:
                quarters = json.loads(text)
            except ValueError:
                quarters = None
            if quarters:
                harvest_holdings(isin, state, quarters, force, run_id)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, help="only first N ISINs")
    ap.add_argument("--isin", action="append", help="specific ISIN(s)")
    ap.add_argument("--tier", action="append",
                    choices=["static", "weekly", "monthly", "quarterly"],
                    help="restrict to tier(s); default all")
    ap.add_argument("--force", action="store_true",
                    help="refetch even fresh/terminal entries")
    ap.add_argument("--if-failed-today", action="store_true",
                    help="exit immediately unless today's latest run failed "
                         "(for the midday retry slot)")
    args = ap.parse_args()

    if args.if_failed_today and should_skip_retry():
        db.close_pool()
        return 0

    tiers = set(args.tier or ["static", "weekly", "monthly", "quarterly"])
    isins = args.isin or db.universe_isins()
    if args.limit:
        isins = isins[:args.limit]
    state = load_state()
    run_id = start_nsdl_run(tiers, args.workers, len(isins))
    print(f"run={run_id} universe={len(isins)} isins, "
          f"tiers={sorted(tiers)}, known rows={len(state)}", flush=True)

    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(harvest_isin, i, state, tiers,
                                 args.force, run_id): i for i in isins}
            for n, fut in enumerate(as_completed(futures), 1):
                isin = futures[fut]
                try:
                    fut.result()
                except NsdlOutage:
                    # Dead upstream: stop the whole pool, not just this ISIN.
                    ex.shutdown(wait=False, cancel_futures=True)
                    raise
                except Exception as exc:  # noqa: BLE001 - keep harvesting
                    print(f"{isin}: ERROR {str(exc)[:80]}", flush=True)
                    _bump("failed")
                if n % 25 == 0 or n == len(isins):
                    el = time.time() - t0
                    eta = el / n * (len(isins) - n)
                    print(f"[{n}/{len(isins)}] {isin} "
                          f"fetched={_stats['fetched']} "
                          f"unchanged={_stats['unchanged']} "
                          f"skipped={_stats['skipped']} "
                          f"failed={_stats['failed']} "
                          f"broken={_stats['broken']} "
                          f"elapsed={el / 60:.1f}m eta={eta / 60:.0f}m",
                          flush=True)
    except BaseException as exc:  # noqa: BLE001 - incl. KeyboardInterrupt
        finish_nsdl_run(run_id, ok=False,
                        notes=f"aborted: {type(exc).__name__}: {exc}"[:500])
        db.close_pool()
        raise

    ok, canary = verdict(_stats)
    finish_nsdl_run(run_id, ok=ok,
                    notes=failure_note(_stats["failed"], _stats["broken"], canary))
    print(f"DONE {json.dumps(_stats)}", flush=True)
    db.close_pool()
    return 0 if ok else 1


def verdict(stats, probe=None) -> tuple[bool, bool | None]:
    """(ok, canary) for a finished run. The canary is only consulted when
    the run attempted work and nothing came back - the one case where the
    counts alone cannot separate an outage from per-record breakage."""
    canary = None
    if stats["fetched"] + stats["unchanged"] == 0 and (stats["failed"] or stats["broken"]):
        canary = (probe or upstream_alive)()
    ok = run_ok(stats["fetched"], stats["unchanged"], stats["failed"], canary)
    return ok, canary


if __name__ == "__main__":
    sys.exit(main())
