"""PostgreSQL storage for the BondLens data platform.

One database (``bondlens``), one pool. Connection comes from the
DATABASE_URL env var (default: local dev database).

Schema lives in migrations/ (applied by scripts/migrate.py, which the
jobs container runs on start). This module only provides the pool and
the write helpers for the universe tables.
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


# --- Users & per-user lists -------------------------------------------------

def upsert_user(sub: str, email: str, name: str | None,
                picture: str | None) -> dict[str, Any]:
    """Create or refresh a user from verified Google claims."""
    with _conn() as conn:
        row = conn.execute(
            """
            INSERT INTO users (google_sub, email, name, picture)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (google_sub) DO UPDATE SET
                email = EXCLUDED.email,
                name = EXCLUDED.name,
                picture = EXCLUDED.picture,
                last_login = now()
            RETURNING id, email, name, picture
            """,
            (sub, email, name, picture),
        ).fetchone()
        assert row is not None
        return row


def get_user(user_id: int) -> dict[str, Any] | None:
    with _conn() as conn:
        return conn.execute(
            "SELECT id, email, name, picture FROM users WHERE id = %s",
            (user_id,),
        ).fetchone()


def get_user_lists(user_id: int) -> dict[str, Any]:
    """{'holdings': [isin...], 'watchlists': [{id, name, isins}...]}.

    holdings = distinct portfolio ISINs (feeds the ◆ held markers)."""
    with _conn() as conn:
        holdings = [
            r["isin"] for r in conn.execute(
                "SELECT isin FROM user_portfolio_lots WHERE user_id = %s "
                "GROUP BY isin ORDER BY min(created_at)",
                (user_id,),
            ).fetchall()
        ]
        rows = conn.execute(
            "SELECT w.id, w.name, i.isin "
            "FROM user_watchlists w "
            "LEFT JOIN user_watchlist_items i ON i.watchlist_id = w.id "
            "WHERE w.user_id = %s "
            "ORDER BY w.created_at, w.id, i.added_at",
            (user_id,),
        ).fetchall()
    watchlists: list[dict[str, Any]] = []
    by_id: dict[int, dict[str, Any]] = {}
    for r in rows:
        wl = by_id.get(r["id"])
        if wl is None:
            wl = {"id": r["id"], "name": r["name"], "isins": []}
            by_id[r["id"]] = wl
            watchlists.append(wl)
        if r["isin"] is not None:
            wl["isins"].append(r["isin"])
    return {"holdings": holdings, "watchlists": watchlists}


MAX_WATCHLISTS = 20


class WatchlistError(Exception):
    """Raised with a user-facing message; app.py maps it to an HTTP error."""
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def create_watchlist(user_id: int, name: str) -> dict[str, Any]:
    name = name.strip()
    if not 1 <= len(name) <= 40:
        raise WatchlistError(400, "List name must be 1-40 characters")
    with _conn() as conn:
        count_row = conn.execute(
            "SELECT COUNT(*) AS n FROM user_watchlists WHERE user_id = %s",
            (user_id,),
        ).fetchone()
        if count_row is not None and count_row["n"] >= MAX_WATCHLISTS:
            raise WatchlistError(400, f"At most {MAX_WATCHLISTS} watchlists")
        row = conn.execute(
            "INSERT INTO user_watchlists (user_id, name) VALUES (%s, %s) "
            "ON CONFLICT (user_id, name) DO NOTHING RETURNING id, name",
            (user_id, name),
        ).fetchone()
    if row is None:
        raise WatchlistError(409, f"A list named '{name}' already exists")
    return {"id": row["id"], "name": row["name"], "isins": []}


def rename_watchlist(user_id: int, watchlist_id: int, name: str) -> None:
    name = name.strip()
    if not 1 <= len(name) <= 40:
        raise WatchlistError(400, "List name must be 1-40 characters")
    with _conn() as conn:
        dup = conn.execute(
            "SELECT 1 FROM user_watchlists "
            "WHERE user_id = %s AND name = %s AND id <> %s",
            (user_id, name, watchlist_id),
        ).fetchone()
        if dup is not None:
            raise WatchlistError(409, f"A list named '{name}' already exists")
        cur = conn.execute(
            "UPDATE user_watchlists SET name = %s "
            "WHERE id = %s AND user_id = %s",
            (name, watchlist_id, user_id),
        )
        if cur.rowcount == 0:
            raise WatchlistError(404, "Watchlist not found")


def delete_watchlist(user_id: int, watchlist_id: int) -> None:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM user_watchlists WHERE id = %s AND user_id = %s",
            (watchlist_id, user_id),
        )
        if cur.rowcount == 0:
            raise WatchlistError(404, "Watchlist not found")


def add_watchlist_item(user_id: int, watchlist_id: int, isin: str) -> None:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO user_watchlist_items (watchlist_id, isin) "
            "SELECT id, %s FROM user_watchlists "
            "WHERE id = %s AND user_id = %s "
            "ON CONFLICT DO NOTHING",
            (isin, watchlist_id, user_id),
        )
        # 0 rows means either duplicate (fine) or foreign list; tell them apart
        if cur.rowcount == 0:
            owned = conn.execute(
                "SELECT 1 FROM user_watchlists WHERE id = %s AND user_id = %s",
                (watchlist_id, user_id),
            ).fetchone()
            if owned is None:
                raise WatchlistError(404, "Watchlist not found")


def remove_watchlist_item(user_id: int, watchlist_id: int, isin: str) -> None:
    with _conn() as conn:
        conn.execute(
            "DELETE FROM user_watchlist_items i USING user_watchlists w "
            "WHERE i.watchlist_id = w.id AND w.id = %s AND w.user_id = %s "
            "AND i.isin = %s",
            (watchlist_id, user_id, isin),
        )


# --- Portfolio lots ---------------------------------------------------------

MAX_LOTS = 500

_LOT_COLS = "id, isin, units, price, purchase_date, platform"


def _lot_row(r: dict[str, Any]) -> dict[str, Any]:
    """numeric -> float for JSON; char(12) isin comes back clean."""
    return {**r, "isin": r["isin"].strip(),
            "units": float(r["units"]),
            "price": float(r["price"]) if r["price"] is not None else None}


def get_portfolio_lots(user_id: int) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_LOT_COLS} FROM user_portfolio_lots "
            "WHERE user_id = %s ORDER BY isin, purchase_date NULLS LAST, id",
            (user_id,),
        ).fetchall()
    return [_lot_row(r) for r in rows]


def _validate_lot(units: float, price: float | None, platform: str | None):
    if not units > 0:
        raise WatchlistError(400, "Units must be positive")
    if price is not None and not price > 0:
        raise WatchlistError(400, "Price must be positive")
    if platform is not None and len(platform) > 40:
        raise WatchlistError(400, "Platform must be at most 40 characters")


def add_lot(user_id: int, isin: str, units: float, price: float | None,
            purchase_date: Any, platform: str | None) -> dict[str, Any]:
    _validate_lot(units, price, platform)
    with _conn() as conn:
        known = conn.execute("SELECT 1 FROM bonds WHERE isin = %s",
                             (isin,)).fetchone()
        if known is None:
            raise WatchlistError(404, "Unknown ISIN")
        count = conn.execute(
            "SELECT count(*) AS n FROM user_portfolio_lots WHERE user_id = %s",
            (user_id,),
        ).fetchone()
        if count is not None and count["n"] >= MAX_LOTS:
            raise WatchlistError(400, f"At most {MAX_LOTS} lots")
        row = conn.execute(
            "INSERT INTO user_portfolio_lots "
            "(user_id, isin, units, price, purchase_date, platform) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            f"RETURNING {_LOT_COLS}",
            (user_id, isin, units, price, purchase_date, platform or None),
        ).fetchone()
        assert row is not None
    return _lot_row(row)


def update_lot(user_id: int, lot_id: int, units: float, price: float | None,
               purchase_date: Any, platform: str | None) -> dict[str, Any]:
    _validate_lot(units, price, platform)
    with _conn() as conn:
        row = conn.execute(
            "UPDATE user_portfolio_lots "
            "SET units = %s, price = %s, purchase_date = %s, platform = %s "
            "WHERE id = %s AND user_id = %s "
            f"RETURNING {_LOT_COLS}",
            (units, price, purchase_date, platform or None, lot_id, user_id),
        ).fetchone()
        if row is None:
            raise WatchlistError(404, "Lot not found")
    return _lot_row(row)


def delete_lot(user_id: int, lot_id: int) -> None:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM user_portfolio_lots WHERE id = %s AND user_id = %s",
            (lot_id, user_id),
        )
        if cur.rowcount == 0:
            raise WatchlistError(404, "Lot not found")


# --- Notifications ----------------------------------------------------------

def get_notifications(user_id: int, limit: int = 50) -> dict[str, Any]:
    """Newest-first notification feed plus the unread count."""
    with _conn() as conn:
        items = conn.execute(
            "SELECT id, kind, severity, isin, title, body, created_at, "
            "read_at FROM user_notifications WHERE user_id = %s "
            "ORDER BY created_at DESC, id DESC LIMIT %s",
            (user_id, limit),
        ).fetchall()
        unread = conn.execute(
            "SELECT count(*) AS n FROM user_notifications "
            "WHERE user_id = %s AND read_at IS NULL",
            (user_id,),
        ).fetchone()
    return {"items": [dict(r) for r in items],
            "unread": unread["n"] if unread else 0}


def mark_notifications_read(user_id: int) -> int:
    """Mark every unread notification read; returns how many flipped."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE user_notifications SET read_at = now() "
            "WHERE user_id = %s AND read_at IS NULL",
            (user_id,),
        )
        return cur.rowcount


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
