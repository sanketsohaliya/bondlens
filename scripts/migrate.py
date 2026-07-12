"""Apply pending SQL migrations from migrations/ in filename order.

Tracking table `schema_migrations` records applied filenames. Each pending
file runs in its own transaction with a lock_timeout so a hung DDL fails
fast instead of blocking the API.

    python scripts/migrate.py            # apply pending
    python scripts/migrate.py --fake     # mark all pending as applied
                                         # without running them (adopting an
                                         # existing database)
    python scripts/migrate.py --status   # list applied/pending
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db  # noqa: E402

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Fully qualified: pg_dump baselines clear the session search_path.
TRACKING = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    faked      BOOLEAN NOT NULL DEFAULT FALSE
)
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true",
                    help="record pending migrations as applied without "
                         "executing them")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql"))
    try:
        with db._conn() as conn:
            conn.execute(TRACKING)
            applied = {r["filename"] for r in conn.execute(
                "SELECT filename FROM public.schema_migrations").fetchall()}

        pending = [p for p in files if p.name not in applied]
        if args.status:
            for p in files:
                print(f"{'applied' if p.name in applied else 'PENDING'}  {p.name}")
            return 0
        if not pending:
            print("migrations: up to date")
            return 0

        for p in pending:
            with db._conn() as conn:
                with conn.transaction():
                    conn.execute("SET LOCAL lock_timeout = '5s'")
                    if not args.fake:
                        conn.execute(p.read_text())  # type: ignore[arg-type]
                    conn.execute(
                        "INSERT INTO public.schema_migrations (filename, faked) "
                        "VALUES (%s, %s)", (p.name, args.fake))
            print(f"{'faked' if args.fake else 'applied'}  {p.name}")
        return 0
    finally:
        db.close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
