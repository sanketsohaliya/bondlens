"""Pre-open build of the yield -> price thresholds every alert rule and
exit target needs today, so the watcher only reads at 09:00.

Cron: 08:30 Mon-Fri (the watcher also builds on its own if this did not
run, e.g. after a restart). Idempotent: cached, still-valid rows are
kept; only missing or invalidated pairs are solved.

    python scripts/build_thresholds.py
    python scripts/build_thresholds.py --workers 2
"""
import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db, threshold_cache  # noqa: E402
from backend import opportunity_rules as rules_db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args()
    today = date.today()
    t0 = time.time()
    try:
        rules = rules_db.all_enabled_rules()
        targets = rules_db.all_enabled_targets()
        candidates = rules_db.candidates_for(rules)
        prices, solved = threshold_cache.ensure(rules, candidates, targets, today,
                                                workers=args.workers)
        pruned = threshold_cache.prune(today - timedelta(days=2))
        unpriceable = sum(1 for p in prices.values() if p is None)
        print(f"thresholds: {len(prices)} pairs ready ({solved} solved now, "
              f"{unpriceable} unpriceable), pruned {pruned}, {time.time() - t0:.1f}s",
              flush=True)
        return 0
    finally:
        db.close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
