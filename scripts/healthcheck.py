"""Run the platform healthcheck and alert via ntfy on problems.

Cron: 08:45 daily (after universe/NSDL/EOD-retry/backup) and 19:45
Mon-Fri (after the evening EOD run).

    python scripts/healthcheck.py           # check, alert if unhealthy
    python scripts/healthcheck.py --ping    # send a test notification
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db, health  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ping", action="store_true",
                    help="send a test notification and exit")
    args = ap.parse_args()

    try:
        if args.ping:
            sent = health.notify("BondLens test", "ntfy wiring works",
                                 priority="default")
            print("ping sent" if sent else "ping skipped (no NTFY_TOPIC)")
            return 0

        problems = health.check()
        if problems:
            body = "\n".join(f"- {p}" for p in problems)
            print(f"UNHEALTHY:\n{body}")
            health.notify(f"BondLens: {len(problems)} problem(s)", body)
        else:
            print("healthy")
        return 0
    finally:
        db.close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
