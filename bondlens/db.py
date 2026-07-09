"""SQLite storage for daily bond snapshots."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

DB_PATH = Path(__file__).resolve().parent.parent / "bonds.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT NOT NULL,
    isin TEXT,
    symbol TEXT,
    series TEXT,
    coupon REAL,
    face_value REAL,
    ltp REAL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    pct_change REAL,
    qty REAL,
    traded_value REAL,
    maturity_date TEXT,
    next_interest_date TEXT,
    credit_rating TEXT,
    rating_agency TEXT,
    ytm REAL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_isin ON snapshots (isin, fetched_at);

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
    amount REAL
);
CREATE INDEX IF NOT EXISTS idx_nsdl_cashflows_isin ON nsdl_cashflows (isin);

CREATE TABLE IF NOT EXISTS debt_master (
    symbol TEXT PRIMARY KEY,
    name TEXT,
    series TEXT,
    face_value REAL,
    coupon REAL,
    maturity_date TEXT
);

CREATE TABLE IF NOT EXISTS gsec_curve (
    fetched_at TEXT,
    tenor REAL,
    yield REAL,
    symbol TEXT,
    traded_value REAL
);

CREATE TABLE IF NOT EXISTS holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    isin TEXT NOT NULL,
    qty REAL NOT NULL,
    buy_price REAL,
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


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def save_snapshot(records: List[Dict]) -> str:
    """Insert a batch of bond records; returns the snapshot timestamp."""
    ts = datetime.now(timezone.utc).isoformat()
    conn = connect()
    with conn:
        conn.executemany(
            f"INSERT INTO snapshots (fetched_at, {', '.join(COLUMNS)}) "
            f"VALUES (?, {', '.join('?' * len(COLUMNS))})",
            [[ts] + [r.get(c) for c in COLUMNS] for r in records],
        )
    conn.close()
    return ts


def latest_snapshot() -> List[Dict]:
    """Return the most recent snapshot's rows."""
    conn = connect()
    row = conn.execute("SELECT MAX(fetched_at) AS ts FROM snapshots").fetchone()
    if not row or not row["ts"]:
        conn.close()
        return []
    rows = conn.execute(
        "SELECT * FROM snapshots WHERE fetched_at = ?", (row["ts"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_debt_master(records: List[Dict]):
    conn = connect()
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO debt_master "
            "(symbol, name, series, face_value, coupon, maturity_date) "
            "VALUES (:symbol, :name, :series, :face_value, :coupon, :maturity_date)",
            records,
        )
    conn.close()


def debt_master() -> Dict[str, Dict]:
    conn = connect()
    rows = conn.execute("SELECT * FROM debt_master").fetchall()
    conn.close()
    return {r["symbol"]: dict(r) for r in rows}


def company_names() -> Dict[str, str]:
    return {sym: r["name"] for sym, r in debt_master().items()}


def price_history(isin: str) -> List[Dict]:
    conn = connect()
    rows = conn.execute(
        "SELECT fetched_at, ltp, ytm, qty, traded_value FROM snapshots "
        "WHERE isin = ? ORDER BY fetched_at",
        (isin,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_watchlist() -> List[str]:
    conn = connect()
    rows = conn.execute("SELECT isin FROM watchlist").fetchall()
    conn.close()
    return [r["isin"] for r in rows]


def add_to_watchlist(isin: str, note: str = ""):
    conn = connect()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO watchlist (isin, note) VALUES (?, ?)",
            (isin, note),
        )
    conn.close()


def remove_from_watchlist(isin: str):
    conn = connect()
    with conn:
        conn.execute("DELETE FROM watchlist WHERE isin = ?", (isin,))
    conn.close()


def save_nsdl(isin: str, status: str, meta: Dict, flows: List[Dict]):
    ts = datetime.now(timezone.utc).isoformat()
    conn = connect()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO nsdl_meta "
            "(isin, fetched_at, status, frequency, frequency_text, coupon_basis) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (isin, ts, status, meta.get("frequency"),
             meta.get("frequency_text"), meta.get("coupon_basis")),
        )
        conn.execute("DELETE FROM nsdl_cashflows WHERE isin = ?", (isin,))
        conn.executemany(
            "INSERT INTO nsdl_cashflows (isin, due_date, record_date, event, amount) "
            "VALUES (?, ?, ?, ?, ?)",
            [(isin, f["due_date"], f["record_date"], f["event"], f["amount"])
             for f in flows],
        )
    conn.close()


def nsdl_fetched_isins() -> Dict[str, str]:
    """Map of isin -> status for all ISINs already attempted."""
    conn = connect()
    rows = conn.execute("SELECT isin, status FROM nsdl_meta").fetchall()
    conn.close()
    return {r["isin"]: r["status"] for r in rows}


def isins_with_flows() -> set:
    conn = connect()
    rows = conn.execute("SELECT DISTINCT isin FROM nsdl_cashflows").fetchall()
    conn.close()
    return {r["isin"] for r in rows}


def nsdl_meta_map() -> Dict[str, Dict]:
    conn = connect()
    rows = conn.execute(
        "SELECT isin, status, frequency, frequency_text, coupon_basis FROM nsdl_meta"
    ).fetchall()
    conn.close()
    return {r["isin"]: dict(r) for r in rows}


def rating_changes() -> Dict[str, Dict]:
    """Per ISIN: the previous distinct credit_rating (raw string) and the
    timestamp when the current value first appeared. ISINs whose rating never
    changed across snapshots are omitted.
    """
    conn = connect()
    rows = conn.execute(
        "SELECT isin, credit_rating, fetched_at FROM snapshots "
        "WHERE isin IS NOT NULL ORDER BY isin, fetched_at"
    ).fetchall()
    conn.close()
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
    conn = connect()
    rows = conn.execute(
        "SELECT isin, MAX(fetched_at) AS ts FROM snapshots "
        "WHERE traded_value > 0 GROUP BY isin"
    ).fetchall()
    conn.close()
    return {r["isin"]: r["ts"] for r in rows}


def save_gsec_curve(points: List[Dict]) -> str:
    ts = datetime.now(timezone.utc).isoformat()
    conn = connect()
    with conn:
        conn.executemany(
            "INSERT INTO gsec_curve (fetched_at, tenor, yield, symbol, traded_value) "
            "VALUES (?, ?, ?, ?, ?)",
            [(ts, p["tenor"], p["yield"], p["symbol"], p["traded_value"])
             for p in points],
        )
    conn.close()
    return ts


def latest_gsec_curve() -> List[Dict]:
    conn = connect()
    row = conn.execute("SELECT MAX(fetched_at) AS ts FROM gsec_curve").fetchone()
    if not row or not row["ts"]:
        conn.close()
        return []
    rows = conn.execute(
        "SELECT tenor, yield, symbol, traded_value FROM gsec_curve "
        "WHERE fetched_at = ? ORDER BY tenor",
        (row["ts"],),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def holdings() -> List[Dict]:
    conn = connect()
    rows = conn.execute("SELECT * FROM holdings ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_holding(isin: str, qty: float, buy_price=None, buy_date=None) -> int:
    conn = connect()
    with conn:
        cur = conn.execute(
            "INSERT INTO holdings (isin, qty, buy_price, buy_date, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (isin, qty, buy_price, buy_date,
             datetime.now(timezone.utc).isoformat()),
        )
        rowid = cur.lastrowid
    conn.close()
    return rowid


def update_holding(holding_id: int, fields: Dict) -> bool:
    allowed = {"qty", "buy_price", "buy_date"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    conn = connect()
    with conn:
        cur = conn.execute(
            f"UPDATE holdings SET {', '.join(f'{k} = ?' for k in updates)} "
            "WHERE id = ?",
            list(updates.values()) + [holding_id],
        )
    conn.close()
    return cur.rowcount > 0


def delete_holding(holding_id: int) -> bool:
    conn = connect()
    with conn:
        cur = conn.execute("DELETE FROM holdings WHERE id = ?", (holding_id,))
    conn.close()
    return cur.rowcount > 0


def nsdl_all_cashflows() -> Dict[str, List[Dict]]:
    conn = connect()
    rows = conn.execute(
        "SELECT isin, due_date, record_date, event, amount FROM nsdl_cashflows"
    ).fetchall()
    conn.close()
    out: Dict[str, List[Dict]] = {}
    for r in rows:
        out.setdefault(r["isin"], []).append(dict(r))
    return out
