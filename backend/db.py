"""PostgreSQL storage for the BondLens data platform.

One database (``bondlens``), one pool. Connection comes from the
DATABASE_URL env var (default: local dev database).

Schema lives in migrations/ (applied by scripts/migrate.py, which the
jobs container runs on start). This module only provides the pool and
the write helpers for the universe/watchlist tables.
"""
import gzip
import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://bondlens:bondlens@localhost:5432/bondlens"
)


_pool: ConnectionPool[Connection[DictRow]] | None = None


def _get_pool() -> ConnectionPool[Connection[DictRow]]:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            DATABASE_URL, min_size=1, max_size=8, open=True,
            connection_class=Connection[DictRow],
            kwargs={"row_factory": dict_row},
        )
    return _pool


@contextmanager
def _conn() -> Iterator[Connection[DictRow]]:
    """Pooled connection; commits on clean exit, rolls back on exception."""
    with _get_pool().connection() as conn:
        yield conn


def close_pool():
    """Close the connection pool cleanly. Call at the end of short-lived
    scripts (e.g. the cron universe build) so worker threads shut down
    without the 'couldn't stop thread' warnings."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


# --- Watchlist --------------------------------------------------------------

def get_watchlist() -> list[dict[str, Any]]:
    with _conn() as conn:
        return conn.execute(
            "SELECT isin, added_at FROM watchlist ORDER BY added_at DESC"
        ).fetchall()


def add_to_watchlist(isin: str):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO watchlist (isin) VALUES (%s) "
            "ON CONFLICT (isin) DO NOTHING",
            (isin,),
        )


def remove_from_watchlist(isin: str):
    with _conn() as conn:
        conn.execute("DELETE FROM watchlist WHERE isin = %s", (isin,))


# --- Bond universe (daily-tracked listed set) -----------------------------

_UNIVERSE_COLS = [
    "isin", "on_nse", "on_bse", "bse_status", "issuer_name",
    "nse_symbol", "nse_series", "bse_scrip_code", "bse_scrip_id",
]


def start_run() -> int:
    """Open a bond_runs row at run start and return its id. The row is
    finalized by save_universe or log_failed_run; if it stays ok IS NULL
    the run crashed mid-way."""
    with _conn() as conn:
        row = conn.execute(
            "INSERT INTO bond_runs (run_at) VALUES (%s) RETURNING id",
            (datetime.now(UTC).isoformat(),),
        ).fetchone()
    assert row is not None  # RETURNING on INSERT always yields a row
    return row["id"]


def save_raw_file(source: str, content: str | bytes,
                  run_id: int | None = None) -> dict[str, Any]:
    """Archive a raw fetched master file (gzipped bytea in raw_files),
    linked to the run that captured it.

    Dedupes on (source, sha256): an unchanged master is stored once, and
    the existing row keeps the run_id of the run that FIRST captured it.
    Returns {source, sha256, size, stored}.
    """
    data = content.encode() if isinstance(content, str) else content
    sha = hashlib.sha256(data).hexdigest()
    with _conn() as conn:
        row = conn.execute(
            "INSERT INTO raw_files "
            "(run_id, fetched_at, source, sha256, size_bytes, content) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (source, sha256) DO NOTHING RETURNING id",
            (run_id, datetime.now(UTC).isoformat(), source, sha,
             len(data), gzip.compress(data)),
        ).fetchone()
    return {"source": source, "sha256": sha, "size": len(data),
            "stored": row is not None}


def log_failed_run(error: str, log: str | None = None,
                   run_id: int | None = None) -> None:
    """Record a failed universe run (fetch or write error) so bond_runs is
    the complete run history, not just the successful ones. Count columns
    stay NULL to distinguish 'failed' from 'ran with zero changes'.

    Finalizes the open run row if run_id is given (failure after
    start_run); otherwise inserts a fresh row (failure during fetch)."""
    now = datetime.now(UTC).isoformat()
    note = f"failed: {error}"
    log_text = f"{log}\n{now} {note}" if log else note
    with _conn() as conn:
        if run_id is not None:
            conn.execute(
                "UPDATE bond_runs SET ok = FALSE, notes = %s, log = %s "
                "WHERE id = %s",
                (note, log_text, run_id),
            )
        else:
            conn.execute(
                "INSERT INTO bond_runs (run_at, ok, notes, log) "
                "VALUES (%s, FALSE, %s, %s)",
                (now, note, log_text),
            )


def save_universe(records: list[dict], run_stats: dict,
                  as_of: str | None = None, max_shrink: float = 0.05,
                  log: str | None = None,
                  run_id: int | None = None) -> dict:
    """Reconcile today's universe against the stored one in one transaction.

    Upserts every incoming ISIN as 'listed' (preserving created_at and
    first_seen), flips ISINs no longer in either master to 'dropped', logs
    each transition to bond_events (one row per changed field),
    and writes a bond_runs summary.

    Guard: a truncated exchange fetch is indistinguishable from a mass
    delisting, so if the incoming set is more than `max_shrink` smaller
    than the stored listed set the run is aborted (logged with ok=false)
    and nothing else is written.

    Returns a dict of counts (added / dropped / relisted / status_change /
    source_change / identifier_change / total), or {'aborted': True, ...}.
    """
    now = datetime.now(UTC).isoformat()
    day = as_of or now[:10]
    incoming = {r["isin"]: r for r in records if r.get("isin")}
    if run_id is None:
        run_id = start_run()

    with _conn() as conn:
        existing = {
            r["isin"]: r for r in conn.execute(
                "SELECT isin, status, on_nse, on_bse, bse_status, "
                "       nse_symbol, nse_series, bse_scrip_code, bse_scrip_id "
                "FROM bonds"
            ).fetchall()
        }

        listed_now = sum(1 for r in existing.values()
                         if r["status"] == "listed")
        if listed_now and len(incoming) < listed_now * (1 - max_shrink):
            note = (f"aborted: incoming {len(incoming)} vs stored listed "
                    f"{listed_now} exceeds max_shrink={max_shrink:.0%}")
            log_text = f"{log}\n{now} {note}" if log else note
            conn.execute(
                "UPDATE bond_runs SET nse_corp = %s, bse_active = %s, "
                " bse_suspended = %s, overlap = %s, total = %s, added = 0, "
                " dropped = 0, relisted = 0, ok = FALSE, notes = %s, "
                " log = %s WHERE id = %s",
                (run_stats.get("nse_corp"), run_stats.get("bse_active"),
                 run_stats.get("bse_suspended"), run_stats.get("overlap"),
                 len(incoming), note, log_text, run_id),
            )
            return {"aborted": True, "reason": note, "total": len(incoming)}

        counts = {"added": 0, "relisted": 0, "status_change": 0,
                  "source_change": 0, "identifier_change": 0}
        events = []  # (day, isin, event_type, field, old_value, new_value)

        def _b(v):
            return "true" if v else "false"

        for isin, rec in incoming.items():
            prev = existing.get(isin)
            if prev is None:
                counts["added"] += 1
                events.append((day, isin, "added", None, None, None))
                continue
            if prev["status"] == "dropped":
                counts["relisted"] += 1
                events.append((day, isin, "relisted", None, None, None))
            if (prev["bse_status"] or None) != (rec.get("bse_status")
                                                or None):
                counts["status_change"] += 1
                events.append((day, isin, "status_change", "bse_status",
                               prev["bse_status"], rec.get("bse_status")))
            for f in ("on_nse", "on_bse"):
                if prev[f] != rec[f]:
                    counts["source_change"] += 1
                    events.append((day, isin, "source_change", f,
                                   _b(prev[f]), _b(rec[f])))
            # Same ISIN, same exchange, but the exchange re-labeled it.
            # None -> value transitions are already covered by source_change.
            for f in ("nse_symbol", "nse_series",
                      "bse_scrip_code", "bse_scrip_id"):
                old, new = prev[f], rec.get(f)
                if old and new and old != new:
                    counts["identifier_change"] += 1
                    events.append((day, isin, "identifier_change", f,
                                   old, new))

        gone = [isin for isin, prev in existing.items()
                if prev["status"] == "listed" and isin not in incoming]
        for isin in gone:
            events.append((day, isin, "dropped", None, None, None))

        # Upsert incoming rows (created_at and first_seen kept on conflict).
        conn.cursor().executemany(
            "INSERT INTO bonds "
            "(isin, status, on_nse, on_bse, bse_status, issuer_name, "
            " nse_symbol, nse_series, bse_scrip_code, bse_scrip_id, "
            " created_at, first_seen, last_seen, updated_at) "
            "VALUES (%(isin)s, 'listed', %(on_nse)s, %(on_bse)s, "
            " %(bse_status)s, %(issuer_name)s, %(nse_symbol)s, "
            " %(nse_series)s, %(bse_scrip_code)s, %(bse_scrip_id)s, "
            " %(now)s, %(now)s, %(now)s, %(now)s) "
            "ON CONFLICT (isin) DO UPDATE SET "
            " status = 'listed', on_nse = EXCLUDED.on_nse, "
            " on_bse = EXCLUDED.on_bse, bse_status = EXCLUDED.bse_status, "
            " issuer_name = COALESCE(EXCLUDED.issuer_name, "
            "                        bonds.issuer_name), "
            " nse_symbol = EXCLUDED.nse_symbol, "
            " nse_series = EXCLUDED.nse_series, "
            " bse_scrip_code = EXCLUDED.bse_scrip_code, "
            " bse_scrip_id = EXCLUDED.bse_scrip_id, "
            " last_seen = EXCLUDED.last_seen, updated_at = EXCLUDED.updated_at",
            [{**{c: rec.get(c) for c in _UNIVERSE_COLS}, "now": now}
             for rec in incoming.values()],
        )

        if gone:
            conn.cursor().executemany(
                "UPDATE bonds SET status = 'dropped', "
                "updated_at = %s WHERE isin = %s",
                [(now, isin) for isin in gone],
            )

        if events:
            conn.cursor().executemany(
                "INSERT INTO bond_events "
                "(event_date, isin, event_type, field, old_value, "
                " new_value, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [(d, i, t, f, o, n, now) for (d, i, t, f, o, n) in events],
            )

        summary = {**counts, "dropped": len(gone), "total": len(incoming)}
        change_line = (f"{now} reconciled: "
                       + " ".join(f"{k}={v}" for k, v in summary.items()))
        log_text = f"{log}\n{change_line}" if log else change_line
        conn.execute(
            "UPDATE bond_runs SET nse_corp = %s, bse_active = %s, "
            " bse_suspended = %s, overlap = %s, total = %s, added = %s, "
            " dropped = %s, relisted = %s, ok = TRUE, notes = %s, log = %s "
            "WHERE id = %s",
            (run_stats.get("nse_corp"), run_stats.get("bse_active"),
             run_stats.get("bse_suspended"), run_stats.get("overlap"),
             len(incoming), counts["added"], len(gone), counts["relisted"],
             run_stats.get("notes"), log_text, run_id),
        )
    return summary


def universe_counts() -> dict:
    """Current universe headline counts."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT "
            " COUNT(*) FILTER (WHERE status='listed') AS listed, "
            " COUNT(*) FILTER (WHERE status='listed' AND on_nse) AS on_nse, "
            " COUNT(*) FILTER (WHERE status='listed' AND on_bse) AS on_bse, "
            " COUNT(*) FILTER (WHERE status='listed' AND on_nse AND on_bse) "
            "   AS both, "
            " COUNT(*) FILTER (WHERE status='dropped') AS dropped "
            "FROM bonds"
        ).fetchone()
    return dict(row) if row else {}


def universe_isins(listed_only: bool = True) -> list[str]:
    """ISINs in the tracked universe (listed only by default)."""
    with _conn() as conn:
        q = "SELECT isin FROM bonds"
        if listed_only:
            q += " WHERE status = 'listed'"
        return [r["isin"] for r in conn.execute(q + " ORDER BY isin").fetchall()]


def recent_universe_events(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        return conn.execute(
            "SELECT event_date, isin, event_type, field, old_value, "
            "       new_value FROM bond_events "
            "ORDER BY id DESC LIMIT %s",
            (limit,),
        ).fetchall()
