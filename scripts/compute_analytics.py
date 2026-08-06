"""Recompute the bond_analytics explorer table.

Cron: runs after the NSDL parse (07:00) and after each EOD parse
(19:00 / 07:15) — the only jobs that change its inputs.

    python scripts/compute_analytics.py [--if-empty]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db  # noqa: E402
from backend.analytics import compute  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--if-empty", action="store_true",
                    help="only compute when the table has no rows "
                         "(container-start backstop)")
    args = ap.parse_args()

    try:
        if args.if_empty and not compute.is_empty():
            print("bond_analytics already populated; skipping")
            return 0
        t0 = time.time()
        n = compute.refresh()
        print(f"bond_analytics refreshed: {n} rows in {time.time() - t0:.1f}s")
        return 0
    finally:
        db.close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
