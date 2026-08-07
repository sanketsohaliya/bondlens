"""Generate portfolio notifications for all users.

Cron: 07:30 daily (after the NSDL parse + analytics) and 19:30 Mon-Fri
(after the EOD parse, for price-anomaly alerts). Idempotent: the
(user_id, dedupe_key) unique constraint swallows repeats.

    python scripts/generate_notifications.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db, notifications  # noqa: E402


def main() -> int:
    try:
        t0 = time.time()
        n = notifications.generate()
        print(f"notifications: {n} new row(s) in {time.time() - t0:.1f}s")
        return 0
    finally:
        db.close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
