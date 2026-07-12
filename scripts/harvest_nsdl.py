"""Harvest every per-ISIN NSDL bdsinfo endpoint for the v2 bond universe.

Raw-first: responses are stored verbatim as JSONB in nsdl_raw,
append-only, so parsing decisions can change later without refetching and
the history of changing data (ratings, holdings, redemptions) is retained.

Endpoint tiers (refresh policy):
  static    isins, coupondetail, instruments, listings, keycontacts
            -- contractual/historical facts; fetched once, skipped forever
  weekly    credit_ratings, defaultdetail, getTDbyIsin, restructuring
            -- event-driven credit data
  monthly   redemptions, keydocuments
            -- slow-moving (buybacks, new documents)
  quarterly quarterenddate + publicshareholderdetails
            -- investor category holdings; ALL quarters are fetched
               (historical), already-captured quarters are frozen

A 400 "No Record Found" is a meaningful terminal answer (clean bond) and
is stored like a success. Identical re-fetches only bump last_checked_at
instead of inserting duplicate payload rows.

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
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, ".")
from bondlens import db  # noqa: E402

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
    "instruments": ("/instruments", "static"),
    "coupondetail": ("/coupondetail", "static"),
    "redemptions": ("/redemptions", "monthly"),
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
_stats = {"fetched": 0, "unchanged": 0, "skipped": 0, "failed": 0}
_stats_lock = threading.Lock()


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
    4xx is a real answer (e.g. 400 'No Record Found'), not a failure."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            r = session(rotate=attempt > 0).get(url, timeout=45)
            if r.status_code < 500:
                return r.status_code, r.text
            print(f"  {tag}: http={r.status_code} retry", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  {tag}: {str(exc)[:60]} retry", flush=True)
        time.sleep(min(5 * (attempt + 1), 20))
    return None, None


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
    now = datetime.now(timezone.utc)
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
            _bump("unchanged")
        else:
            conn.execute(
                "INSERT INTO nsdl_raw (isin, endpoint, quarter, run_id, "
                " fetched_at, last_checked_at, http_status, sha256, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (isin, endpoint, quarter, run_id, now, now, status, sha,
                 json.dumps(payload)))
            _bump("fetched")
    return status, sha, now


def start_nsdl_run(tiers, workers, n_isins):
    """Open an nsdl_runs row; ok stays NULL until finalized, so a crashed
    run is queryable (same convention as bond_runs)."""
    with db._conn() as conn:
        row = conn.execute(
            "INSERT INTO nsdl_runs (run_at, tiers, workers, isins) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (datetime.now(timezone.utc), ",".join(sorted(tiers)),
             workers, n_isins)).fetchone()
    return row["id"]


def finish_nsdl_run(run_id, ok, notes=None):
    with _db_lock, db._conn() as conn:
        conn.execute(
            "UPDATE nsdl_runs SET finished_at = %s, fetched = %s, "
            " unchanged = %s, skipped = %s, failed = %s, ok = %s, "
            " notes = %s WHERE id = %s",
            (datetime.now(timezone.utc), _stats["fetched"],
             _stats["unchanged"], _stats["skipped"], _stats["failed"],
             ok, notes, run_id))


def due(prev, tier, force):
    """Should this (isin, endpoint) be fetched now?"""
    if force or prev is None:
        return True
    if tier == "static":
        return False  # terminal answer already stored
    return prev[2] < datetime.now(timezone.utc) - MAX_AGE[tier]


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
        if name == "quarterenddate" and status == 200:
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
    args = ap.parse_args()

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
                          f"elapsed={el / 60:.1f}m eta={eta / 60:.0f}m",
                          flush=True)
    except BaseException as exc:  # noqa: BLE001 - incl. KeyboardInterrupt
        finish_nsdl_run(run_id, ok=False,
                        notes=f"aborted: {type(exc).__name__}: {exc}"[:500])
        db.close_pool()
        raise

    ok = _stats["failed"] == 0
    finish_nsdl_run(run_id, ok=ok,
                    notes=None if ok else f"{_stats['failed']} fetch failures")
    print(f"DONE {json.dumps(_stats)}", flush=True)
    db.close_pool()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
