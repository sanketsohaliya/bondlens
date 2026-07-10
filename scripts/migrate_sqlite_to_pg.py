"""One-time migration: copy all data from the legacy bonds.db (SQLite) into
PostgreSQL (DATABASE_URL). Idempotent-ish: refuses to run if the target
snapshots table already has rows, so it can't double-import.

Run from the repo root:  PYTHONPATH=. .venv/bin/python scripts/migrate_sqlite_to_pg.py
"""
import sqlite3
import sys
from pathlib import Path

from bondlens import db

SQLITE_PATH = Path(__file__).resolve().parent.parent / "bonds.db"

# table -> columns (identical names on both sides; ids are re-generated for
# serial tables, which is safe because nothing references them across tables)
TABLES = {
    "snapshots": ["fetched_at"] + db.COLUMNS,
    "watchlist": ["isin", "note"],
    "nsdl_meta": ["isin", "fetched_at", "status", "frequency",
                  "frequency_text", "coupon_basis"],
    "nsdl_cashflows": ["isin", "due_date", "record_date", "event", "amount"],
    "debt_master": ["symbol", "name", "series", "face_value", "coupon",
                    "maturity_date"],
    "gsec_curve": ["fetched_at", "tenor", "yield", "symbol", "traded_value"],
    "nsdl_ratings": ["isin", "agency", "rating", "outlook", "rating_action",
                     "changed_at", "prev_rating", "press_release"],
    "nsdl_ratings_meta": ["isin", "fetched_at"],
    "holdings": ["isin", "qty", "buy_price", "buy_date", "created_at"],
}


def main():
    if not SQLITE_PATH.exists():
        sys.exit(f"{SQLITE_PATH} not found — nothing to migrate")
    src = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row

    with db._conn() as conn:
        existing = conn.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()
        if existing["n"]:
            sys.exit(f"Target already has {existing['n']} snapshot rows — "
                     "refusing to double-import. Truncate first if intended.")

    for table, cols in TABLES.items():
        rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
        if not rows:
            print(f"{table:20s} 0 rows")
            continue
        with db._conn() as conn:
            conn.cursor().executemany(
                f"INSERT INTO {table} ({', '.join(cols)}) "
                f"VALUES ({', '.join(['%s'] * len(cols))})",
                [[r[c] for c in cols] for r in rows],
            )
        print(f"{table:20s} {len(rows)} rows")

    src.close()
    print("done — verify, then archive bonds.db")


if __name__ == "__main__":
    main()
