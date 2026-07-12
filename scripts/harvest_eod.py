"""Harvest EOD bond-trade files from NSE and BSE.

Raw-first: each day's files are stored verbatim (gzipped) in
eod_raw, append-only, so parsing decisions can change later
without refetching. Three fetches per trading day covering four streams:

  nse_cm    NSE CM bhavcopy (UDiFF zip; legacy zip before Jul 2024)
            -- exchange order-book ("retail") trades, all instruments
  nse_cbm   NSE CBM trades bhavcopy (cbm_trd csv)
            -- OTC/RFQ reported corp-bond trades, with yields
  bse_debt  BSE debt bhavcopy zip (fgroup + icdm + wdm csvs)
            -- BSE order-book and OTC reported trades

A 404 (NSE) or HTML-with-200 (BSE) means "no file for this date"
(holiday/weekend/not yet published) and is recorded with http_status so
holidays are not refetched forever; today's misses are retried on the
next run because absent rows for the last few days are re-checked.

Usage:
  .venv/bin/python scripts/harvest_eod.py          # resume: last stored day -> today
  .venv/bin/python scripts/harvest_eod.py --from 2014-01-01 --to 2026-04-12
  .venv/bin/python scripts/harvest_eod.py --force  # refetch
"""
import argparse
import gzip
import hashlib
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import db  # noqa: E402

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "*/*",
}
DELAY = 0.3
MAX_ATTEMPTS = 3
RECHECK_ABSENT_DAYS = 5   # re-try recent "absent" days (publication lag)
UDIFF_START = date(2024, 7, 8)  # first day the UDiFF CM bhavcopy exists

SCHEMA = """
CREATE TABLE IF NOT EXISTS eod_runs (
    id BIGSERIAL PRIMARY KEY,
    run_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    date_from DATE,
    date_to DATE,
    fetched INTEGER,
    absent INTEGER,
    skipped INTEGER,
    failed INTEGER,
    ok BOOLEAN,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS eod_raw (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('nse_cm', 'nse_cbm', 'bse_debt')),
    trade_date DATE NOT NULL,
    run_id BIGINT REFERENCES eod_runs (id),
    fetched_at TIMESTAMPTZ NOT NULL,
    url TEXT,
    http_status INTEGER NOT NULL,
    sha256 TEXT,
    content BYTEA,
    UNIQUE (source, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_eod_raw_key ON eod_raw (source, trade_date);
"""


def urls_for(source: str, day: date):
    """Candidate URLs for a source+date, tried in order."""
    ymd = day.strftime("%Y%m%d")
    dmy = day.strftime("%d%m%Y")
    if source == "nse_cm":
        udiff = ("https://nsearchives.nseindia.com/content/cm/"
                 f"BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip")
        legacy = ("https://nsearchives.nseindia.com/content/historical/"
                  f"EQUITIES/{day.year}/{day.strftime('%b').upper()}/"
                  f"cm{day.strftime('%d%b%Y').upper()}bhav.csv.zip")
        return [udiff, legacy] if day >= UDIFF_START else [legacy]
    if source == "nse_cbm":
        return ["https://nsearchives.nseindia.com/archives/debt/cbm/"
                f"cbm_trd{ymd}.csv"]
    if source == "bse_debt":
        return ["https://www.bseindia.com/download/BhavCopy/Debt/"
                f"DEBTBHAVCOPY{dmy}.zip"]
    raise ValueError(source)


def looks_valid(source: str, content: bytes) -> bool:
    """BSE serves its homepage with HTTP 200 for missing files; zips must
    start with the PK magic, csvs must not be HTML."""
    if not content:
        return False
    if source in ("nse_cm", "bse_debt"):
        return content[:2] == b"PK"
    head = content[:200].lstrip().lower()
    return not head.startswith((b"<!doctype", b"<html"))


def fetch(session, url):
    """GET with retries; returns (status, content) or (None, None)."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            r = session.get(url, timeout=45)
            if r.status_code < 500:
                return r.status_code, r.content
        except requests.RequestException as exc:
            print(f"  {url.rsplit('/', 1)[-1]}: {str(exc)[:60]} retry",
                  flush=True)
        time.sleep(2 * (attempt + 1))
    return None, None


def load_state():
    """(source, trade_date) -> http_status of the stored row."""
    with db._conn() as conn:
        conn.execute(SCHEMA)
        rows = conn.execute(
            "SELECT source, trade_date, http_status FROM eod_raw"
        ).fetchall()
    return {(r["source"], r["trade_date"]): r["http_status"] for r in rows}


def store(source, day, url, status, content, run_id, replace):
    now = datetime.now(UTC)
    sha = hashlib.sha256(content).hexdigest() if content else None
    blob = gzip.compress(content) if content else None
    with db._conn() as conn:
        if replace:
            conn.execute(
                "DELETE FROM eod_raw WHERE source = %s AND trade_date = %s",
                (source, day))
        conn.execute(
            "INSERT INTO eod_raw (source, trade_date, run_id, fetched_at, "
            " url, http_status, sha256, content) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (source, trade_date) DO NOTHING",
            (source, day, run_id, now, url, status, sha, blob))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="date_from",
                    type=lambda s: date.fromisoformat(s))
    ap.add_argument("--to", dest="date_to",
                    type=lambda s: date.fromisoformat(s))
    ap.add_argument("--force", action="store_true",
                    help="refetch days that already have a stored file")
    args = ap.parse_args()

    today = date.today()
    date_to = args.date_to or today
    state = load_state()
    date_from = args.date_from
    if date_from is None:
        last = max((d for _, d in state), default=None)
        date_from = last or (today - timedelta(days=1))

    stats = {"fetched": 0, "absent": 0, "skipped": 0, "failed": 0}
    with db._conn() as conn:
        run_row = conn.execute(
            "INSERT INTO eod_runs (run_at, date_from, date_to) "
            "VALUES (%s, %s, %s) RETURNING id",
            (datetime.now(UTC), date_from, date_to)
        ).fetchone()
    assert run_row is not None
    run_id = run_row["id"]

    session = requests.Session()
    session.headers.update(HEADERS)

    day = date_from
    while day <= date_to:
        if day.weekday() >= 5:
            day += timedelta(days=1)
            continue
        for source in ("nse_cm", "nse_cbm", "bse_debt"):
            prev = state.get((source, day))
            recheck = (prev is not None and prev != 200 and
                       (today - day).days <= RECHECK_ABSENT_DAYS)
            if prev is not None and not args.force and not recheck:
                stats["skipped"] += 1
                continue
            got = False
            status, url = None, ""
            for url in urls_for(source, day):
                status, content = fetch(session, url)
                if status is None:
                    continue
                if status == 200 and content is not None \
                        and looks_valid(source, content):
                    store(source, day, url, 200, content, run_id,
                          replace=args.force or recheck)
                    stats["fetched"] += 1
                    got = True
                    break
                time.sleep(DELAY)
            if not got:
                if status is None:
                    stats["failed"] += 1
                else:
                    # remember the miss (holiday / not yet published)
                    store(source, day, url, status if status != 200 else 404,
                          None, run_id, replace=recheck)
                    stats["absent"] += 1
            time.sleep(DELAY)
        print(f"{day} done "
              f"(fetched={stats['fetched']} absent={stats['absent']} "
              f"skipped={stats['skipped']} failed={stats['failed']})",
              flush=True)
        day += timedelta(days=1)

    ok = stats["failed"] == 0
    with db._conn() as conn:
        conn.execute(
            "UPDATE eod_runs SET finished_at = %s, fetched = %s, "
            " absent = %s, skipped = %s, failed = %s, ok = %s WHERE id = %s",
            (datetime.now(UTC), stats["fetched"], stats["absent"],
             stats["skipped"], stats["failed"], ok, run_id))
    print(f"DONE {stats}", flush=True)
    db.close_pool()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
