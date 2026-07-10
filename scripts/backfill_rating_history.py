"""One-time backfill of the nsdl_rating_history archive.

Rating history is normally captured whenever an ISIN's ratings are
(re)fetched, but ISINs fetched before the archive existed won't have a
trail until their next 30-day refresh. This hits only the lightweight
credit-ratings endpoint (no schedules) for every ISIN in the snapshot and
holdings that has no archived history yet, so it is safe to re-run and
resumes where it left off.

Usage: PYTHONPATH=. .venv/bin/python scripts/backfill_rating_history.py
"""
import sys
import time

from bondlens import db, nsdl

DELAY_SECONDS = 2  # ratings endpoint only — lighter than the full fetch


def main():
    universe = sorted(
        {r["isin"] for r in db.latest_snapshot() if r["isin"]}
        | {h["isin"] for h in db.holdings()}
    )
    with db._conn() as conn:  # noqa: SLF001 - one-off maintenance query
        have = {r["isin"] for r in conn.execute(
            "SELECT DISTINCT isin FROM nsdl_rating_history").fetchall()}
    pending = [i for i in universe if i not in have]
    print(f"universe={len(universe)} archived={len(have)} pending={len(pending)}",
          flush=True)
    errors = 0
    for n, isin in enumerate(pending, 1):
        try:
            current, history = nsdl.fetch_rating_data(isin)
            db.save_nsdl_ratings(isin, current)
            db.save_nsdl_rating_history(isin, history)
        except Exception as exc:  # noqa: BLE001 - log and continue
            errors += 1
            print(f"[{n}/{len(pending)}] {isin} ERROR {exc}", flush=True)
        else:
            if n % 50 == 0:
                print(f"[{n}/{len(pending)}] ok (errors={errors})", flush=True)
        time.sleep(DELAY_SECONDS)
    print(f"done: {len(pending)} fetched, {errors} errors", flush=True)
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
