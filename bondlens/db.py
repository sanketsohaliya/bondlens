"""PostgreSQL storage for bond snapshots, NSDL cache, watchlist and holdings.

Connection comes from the DATABASE_URL env var (default: local dev database).
Snapshots and the G-Sec curve are append-only — history is kept forever by
design; nothing in the app prunes them.
"""
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, List

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://bondlens:bondlens@localhost:5432/bondlens"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id BIGSERIAL PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    isin TEXT,
    symbol TEXT,
    series TEXT,
    coupon DOUBLE PRECISION,
    face_value DOUBLE PRECISION,
    ltp DOUBLE PRECISION,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    pct_change DOUBLE PRECISION,
    qty DOUBLE PRECISION,
    traded_value DOUBLE PRECISION,
    maturity_date TEXT,
    next_interest_date TEXT,
    credit_rating TEXT,
    rating_agency TEXT,
    ytm DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_snapshots_isin ON snapshots (isin, fetched_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_fetched_at ON snapshots (fetched_at);

CREATE TABLE IF NOT EXISTS watchlist (
    isin TEXT PRIMARY KEY,
    note TEXT
);

CREATE TABLE IF NOT EXISTS nsdl_meta (
    isin TEXT PRIMARY KEY,
    fetched_at TEXT,
    status TEXT,
    frequency TEXT,
    frequency_text TEXT,
    coupon_basis TEXT
);

CREATE TABLE IF NOT EXISTS nsdl_cashflows (
    isin TEXT,
    due_date TEXT,
    record_date TEXT,
    event TEXT,
    amount DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_nsdl_cashflows_isin ON nsdl_cashflows (isin);

CREATE TABLE IF NOT EXISTS debt_master (
    symbol TEXT PRIMARY KEY,
    name TEXT,
    series TEXT,
    face_value DOUBLE PRECISION,
    coupon DOUBLE PRECISION,
    maturity_date TEXT
);

CREATE TABLE IF NOT EXISTS gsec_curve (
    fetched_at TEXT,
    tenor DOUBLE PRECISION,
    yield DOUBLE PRECISION,
    symbol TEXT,
    traded_value DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS nsdl_ratings (
    isin TEXT NOT NULL,
    agency TEXT,
    rating TEXT,
    outlook TEXT,
    rating_action TEXT,
    changed_at TEXT,
    prev_rating TEXT,
    press_release TEXT
);
CREATE INDEX IF NOT EXISTS idx_nsdl_ratings_isin ON nsdl_ratings (isin);

-- Fetch log, separate from the rows: an ISIN with no current ratings still
-- counts as fetched (so staleness checks don't retry it forever).
CREATE TABLE IF NOT EXISTS nsdl_ratings_meta (
    isin TEXT PRIMARY KEY,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS holdings (
    id BIGSERIAL PRIMARY KEY,
    isin TEXT NOT NULL,
    qty DOUBLE PRECISION NOT NULL,
    buy_price DOUBLE PRECISION,
    buy_date TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_holdings_isin ON holdings (isin);
"""

COLUMNS = [
    "isin", "symbol", "series", "coupon", "face_value", "ltp", "open", "high",
    "low", "close", "pct_change", "qty", "traded_value", "maturity_date",
    "next_interest_date", "credit_rating", "rating_agency", "ytm",
]

_pool: ConnectionPool = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            DATABASE_URL, min_size=1, max_size=8, open=True,
            kwargs={"row_factory": dict_row},
        )
        with _pool.connection() as conn:
            conn.execute(SCHEMA)
    return _pool


@contextmanager
def _conn():
    """Pooled connection; commits on clean exit, rolls back on exception."""
    with _get_pool().connection() as conn:
        yield conn


def save_snapshot(records: List[Dict]) -> str:
    """Insert a batch of bond records; returns the snapshot timestamp."""
    ts = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.cursor().executemany(
            f"INSERT INTO snapshots (fetched_at, {', '.join(COLUMNS)}) "
            f"VALUES (%s, {', '.join(['%s'] * len(COLUMNS))})",
            [[ts] + [r.get(c) for c in COLUMNS] for r in records],
        )
    return ts


def latest_snapshot() -> List[Dict]:
    """Return the most recent snapshot's rows."""
    with _conn() as conn:
        row = conn.execute("SELECT MAX(fetched_at) AS ts FROM snapshots").fetchone()
        if not row or not row["ts"]:
            return []
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE fetched_at = %s", (row["ts"],)
        ).fetchall()
    return rows


def save_debt_master(records: List[Dict]):
    with _conn() as conn:
        conn.cursor().executemany(
            "INSERT INTO debt_master "
            "(symbol, name, series, face_value, coupon, maturity_date) "
            "VALUES (%(symbol)s, %(name)s, %(series)s, %(face_value)s, "
            "        %(coupon)s, %(maturity_date)s) "
            "ON CONFLICT (symbol) DO UPDATE SET "
            "name = EXCLUDED.name, series = EXCLUDED.series, "
            "face_value = EXCLUDED.face_value, coupon = EXCLUDED.coupon, "
            "maturity_date = EXCLUDED.maturity_date",
            records,
        )


def debt_master() -> Dict[str, Dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM debt_master").fetchall()
    return {r["symbol"]: r for r in rows}


def company_names() -> Dict[str, str]:
    return {sym: r["name"] for sym, r in debt_master().items()}


def price_history(isin: str) -> List[Dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT fetched_at, ltp, ytm, qty, traded_value FROM snapshots "
            "WHERE isin = %s ORDER BY fetched_at",
            (isin,),
        ).fetchall()
    return rows


def get_watchlist() -> List[str]:
    with _conn() as conn:
        rows = conn.execute("SELECT isin FROM watchlist").fetchall()
    return [r["isin"] for r in rows]


def add_to_watchlist(isin: str, note: str = ""):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO watchlist (isin, note) VALUES (%s, %s) "
            "ON CONFLICT (isin) DO UPDATE SET note = EXCLUDED.note",
            (isin, note),
        )


def remove_from_watchlist(isin: str):
    with _conn() as conn:
        conn.execute("DELETE FROM watchlist WHERE isin = %s", (isin,))


def save_nsdl(isin: str, status: str, meta: Dict, flows: List[Dict]):
    ts = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO nsdl_meta "
            "(isin, fetched_at, status, frequency, frequency_text, coupon_basis) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (isin) DO UPDATE SET "
            "fetched_at = EXCLUDED.fetched_at, status = EXCLUDED.status, "
            "frequency = EXCLUDED.frequency, "
            "frequency_text = EXCLUDED.frequency_text, "
            "coupon_basis = EXCLUDED.coupon_basis",
            (isin, ts, status, meta.get("frequency"),
             meta.get("frequency_text"), meta.get("coupon_basis")),
        )
        conn.execute("DELETE FROM nsdl_cashflows WHERE isin = %s", (isin,))
        conn.cursor().executemany(
            "INSERT INTO nsdl_cashflows (isin, due_date, record_date, event, amount) "
            "VALUES (%s, %s, %s, %s, %s)",
            [(isin, f["due_date"], f["record_date"], f["event"], f["amount"])
             for f in flows],
        )


def nsdl_fetched_isins() -> Dict[str, str]:
    """Map of isin -> status for all ISINs already attempted."""
    with _conn() as conn:
        rows = conn.execute("SELECT isin, status FROM nsdl_meta").fetchall()
    return {r["isin"]: r["status"] for r in rows}


def isins_with_flows() -> set:
    with _conn() as conn:
        rows = conn.execute("SELECT DISTINCT isin FROM nsdl_cashflows").fetchall()
    return {r["isin"] for r in rows}


def nsdl_meta_map() -> Dict[str, Dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT isin, status, frequency, frequency_text, coupon_basis "
            "FROM nsdl_meta"
        ).fetchall()
    return {r["isin"]: r for r in rows}


def rating_changes() -> Dict[str, Dict]:
    """Per ISIN: the previous distinct credit_rating (raw string) and the
    timestamp when the current value first appeared. ISINs whose rating never
    changed across snapshots are omitted.
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT isin, credit_rating, fetched_at FROM snapshots "
            "WHERE isin IS NOT NULL ORDER BY isin, fetched_at"
        ).fetchall()
    hist: Dict[str, List] = {}
    for r in rows:
        hist.setdefault(r["isin"], []).append((r["fetched_at"], r["credit_rating"]))
    out = {}
    for isin, seq in hist.items():
        current = seq[-1][1]
        previous = None
        changed_at = seq[-1][0]
        for ts, val in reversed(seq):
            if val != current:
                previous = val
                break
            changed_at = ts
        if previous is not None:
            out[isin] = {"previous": previous, "changed_at": changed_at}
    return out


def last_traded_map() -> Dict[str, str]:
    """Per ISIN: timestamp of the most recent snapshot where it actually traded."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT isin, MAX(fetched_at) AS ts FROM snapshots "
            "WHERE traded_value > 0 GROUP BY isin"
        ).fetchall()
    return {r["isin"]: r["ts"] for r in rows}


def save_gsec_curve(points: List[Dict]) -> str:
    ts = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.cursor().executemany(
            "INSERT INTO gsec_curve (fetched_at, tenor, yield, symbol, traded_value) "
            "VALUES (%s, %s, %s, %s, %s)",
            [(ts, p["tenor"], p["yield"], p["symbol"], p["traded_value"])
             for p in points],
        )
    return ts


def latest_gsec_curve() -> List[Dict]:
    with _conn() as conn:
        row = conn.execute("SELECT MAX(fetched_at) AS ts FROM gsec_curve").fetchone()
        if not row or not row["ts"]:
            return []
        rows = conn.execute(
            "SELECT tenor, yield, symbol, traded_value FROM gsec_curve "
            "WHERE fetched_at = %s ORDER BY tenor",
            (row["ts"],),
        ).fetchall()
    return rows


def holdings() -> List[Dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM holdings ORDER BY id").fetchall()
    return rows


def add_holding(isin: str, qty: float, buy_price=None, buy_date=None) -> int:
    with _conn() as conn:
        row = conn.execute(
            "INSERT INTO holdings (isin, qty, buy_price, buy_date, created_at) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (isin, qty, buy_price, buy_date,
             datetime.now(timezone.utc).isoformat()),
        ).fetchone()
    return row["id"]


def update_holding(holding_id: int, fields: Dict) -> bool:
    allowed = {"qty", "buy_price", "buy_date"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE holdings SET {', '.join(f'{k} = %s' for k in updates)} "
            "WHERE id = %s",
            list(updates.values()) + [holding_id],
        )
        return cur.rowcount > 0


def delete_holding(holding_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM holdings WHERE id = %s", (holding_id,))
        return cur.rowcount > 0


def save_nsdl_ratings(isin: str, ratings: List[Dict]):
    with _conn() as conn:
        conn.execute("DELETE FROM nsdl_ratings WHERE isin = %s", (isin,))
        conn.cursor().executemany(
            "INSERT INTO nsdl_ratings "
            "(isin, agency, rating, outlook, rating_action, changed_at, "
            " prev_rating, press_release) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [(isin, x["agency"], x["rating"], x["outlook"], x["action"],
              x["changed_at"], x["prev_rating"], x["press_release"])
             for x in ratings],
        )
        conn.execute(
            "INSERT INTO nsdl_ratings_meta (isin, fetched_at) VALUES (%s, %s) "
            "ON CONFLICT (isin) DO UPDATE SET fetched_at = EXCLUDED.fetched_at",
            (isin, datetime.now(timezone.utc).isoformat()),
        )


def nsdl_ratings_map() -> Dict[str, List[Dict]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM nsdl_ratings ORDER BY isin, agency"
        ).fetchall()
    out: Dict[str, List[Dict]] = {}
    for r in rows:
        out.setdefault(r["isin"], []).append(r)
    return out


def nsdl_ratings_fetched() -> Dict[str, str]:
    """Map of isin -> fetched_at ISO timestamp for staleness checks."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT isin, fetched_at FROM nsdl_ratings_meta"
        ).fetchall()
    return {r["isin"]: r["fetched_at"] for r in rows}


def nsdl_all_cashflows() -> Dict[str, List[Dict]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT isin, due_date, record_date, event, amount FROM nsdl_cashflows"
        ).fetchall()
    out: Dict[str, List[Dict]] = {}
    for r in rows:
        out.setdefault(r["isin"], []).append(r)
    return out
