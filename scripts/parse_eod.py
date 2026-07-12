"""Parse eod_raw files into the eod_trades table — incrementally.

Unlike the NSDL parse layer (truncate + rebuild), eod_trades is built once
from the deep backfill and then only appended to: each run parses raw
files that have no parsed_at stamp yet and upserts their rows, so the
daily cron does 3 files of work, not the whole history.

Only rows whose ISIN is in the bonds universe are kept — everything else
(equities, G-secs, SGBs, CP/CD, non-universe corp paper) is discarded.

One row per (isin, trade_date, exchange, segment):
  retail = exchange order book   (OHLC, volume, trade count; dirty ₹ price)
  otc    = reported OTC/RFQ deals (LTP, WAP, yields; clean % price)
Turnover is normalized to ₹ (files variously use ₹ and ₹ lakh).

    .venv/bin/python scripts/parse_eod.py            # new files only
    .venv/bin/python scripts/parse_eod.py --rebuild  # full re-parse
"""
import argparse
import csv
import gzip
import io
import sys
import zipfile
from pathlib import Path
from typing import LiteralString

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bondlens import db  # noqa: E402

LAKH = 100_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS eod_trades (
    isin TEXT NOT NULL REFERENCES bonds (isin),
    trade_date DATE NOT NULL,
    exchange TEXT NOT NULL CHECK (exchange IN ('NSE', 'BSE')),
    segment TEXT NOT NULL CHECK (segment IN ('retail', 'otc')),
    price_basis TEXT NOT NULL
        CHECK (price_basis IN ('dirty_rupees', 'clean_pct')),
    open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC,
    prev_close NUMERIC,
    last_price NUMERIC,
    wap NUMERIC,                     -- weighted average price
    last_yield NUMERIC,              -- YTM of last trade (otc only)
    wap_yield NUMERIC,               -- weighted average YTM (otc only)
    volume NUMERIC,                  -- units traded (retail only)
    turnover NUMERIC,                -- ₹
    num_trades INTEGER,              -- executed trades (retail only)
    raw_id BIGINT,                   -- provenance -> eod_raw.id
    PRIMARY KEY (isin, trade_date, exchange, segment)
);
CREATE INDEX IF NOT EXISTS idx_eod_trades_date ON eod_trades (trade_date);
ALTER TABLE eod_raw ADD COLUMN IF NOT EXISTS parsed_at TIMESTAMPTZ;
"""

UPSERT_SET: LiteralString = ", ".join(
    f"{c} = EXCLUDED.{c}"
    for c in ("price_basis", "open", "high", "low", "close",
              "prev_close", "last_price", "wap", "last_yield",
              "wap_yield", "volume", "turnover", "num_trades",
              "raw_id"))


def _text(b: bytes) -> str:
    """Decode a csv payload defensively: old exchange files occasionally
    contain NUL bytes and mixed line endings that break the csv module."""
    return (b.decode("utf-8", "replace").replace("\x00", "")
            .replace("\r\n", "\n").replace("\r", "\n"))


def _num(v):
    v = (v or "").strip().replace(",", "")
    if v in ("", "-", "NA", "N.A."):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _isin(v):
    return (v or "").strip().upper()


# ---------------------------------------------------------------- parsers
# Each yields dicts keyed by the trades columns (minus raw_id).

def parse_nse_cm(content: bytes, universe):
    """UDiFF or legacy CM bhavcopy zip -> NSE retail rows."""
    zf = zipfile.ZipFile(io.BytesIO(content))
    names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
    text = _text(zf.read((names or zf.namelist())[0]))
    rdr = csv.DictReader(io.StringIO(text))
    fields = {f.strip(): f for f in rdr.fieldnames or []}

    def g(row, *names):
        for n in names:
            if n in fields:
                return row.get(fields[n])
        return None

    for row in rdr:
        isin = _isin(g(row, "ISIN"))
        if isin not in universe:
            continue
        yield {
            "isin": isin, "exchange": "NSE", "segment": "retail",
            "price_basis": "dirty_rupees",
            "open": _num(g(row, "OpnPric", "OPEN")),
            "high": _num(g(row, "HghPric", "HIGH")),
            "low": _num(g(row, "LwPric", "LOW")),
            "close": _num(g(row, "ClsPric", "CLOSE")),
            "prev_close": _num(g(row, "PrvsClsgPric", "PREVCLOSE")),
            "last_price": _num(g(row, "LastPric", "LAST")),
            "wap": None, "last_yield": None, "wap_yield": None,
            "volume": _num(g(row, "TtlTradgVol", "TOTTRDQTY")),
            "turnover": _num(g(row, "TtlTrfVal", "TOTTRDVAL")),
            "num_trades": (int(_num(g(row, "TtlNbOfTxsExctd",
                                      "TOTALTRADES")) or 0) or None),
        }


def parse_nse_cbm(content: bytes, universe):
    """cbm_trd csv -> NSE OTC rows (clean % prices, yields, ₹ lakh)."""
    rdr = csv.DictReader(io.StringIO(_text(content)))
    fields = {f.strip(): f for f in rdr.fieldnames or []}

    def g(row, key):
        for name, orig in fields.items():
            if name.lower().startswith(key):
                return row.get(orig)
        return None

    for row in rdr:
        isin = _isin(g(row, "isin"))
        if isin not in universe:
            continue
        tot_val = _num(g(row, "total trade value"))
        yield {
            "isin": isin, "exchange": "NSE", "segment": "otc",
            "price_basis": "clean_pct",
            "open": None, "high": None, "low": None, "close": None,
            "prev_close": None,
            "last_price": _num(g(row, "last trade price")),
            "wap": _num(g(row, "weighted average price")),
            "last_yield": _num(g(row, "last trade yield")),
            "wap_yield": _num(g(row, "weighted average yield")),
            "volume": None,
            "turnover": tot_val * LAKH if tot_val is not None else None,
            "num_trades": None,
        }


def parse_bse_debt(content: bytes, universe):
    """BSE debt zip -> fgroup (retail) + icdm (otc) rows."""
    zf = zipfile.ZipFile(io.BytesIO(content))
    for name in zf.namelist():
        low = name.lower()
        text = _text(zf.read(name))
        rdr = csv.DictReader(io.StringIO(text))
        fields = {f.strip().lower(): f for f in rdr.fieldnames or []}

        def g(row, key, _fields=fields):
            for n, orig in _fields.items():
                if n.startswith(key):
                    return row.get(orig)
            return None

        if low.startswith("fgroup"):
            for row in rdr:
                isin = _isin(g(row, "isin"))
                if isin not in universe:
                    continue
                turnover = _num(g(row, "total turnover"))
                yield {
                    "isin": isin, "exchange": "BSE", "segment": "retail",
                    "price_basis": "dirty_rupees",
                    "open": _num(g(row, "open price")),
                    "high": _num(g(row, "high price")),
                    "low": _num(g(row, "low price")),
                    "close": _num(g(row, "close price")),
                    "prev_close": None,
                    "last_price": _num(g(row, "close price")),
                    "wap": None, "last_yield": None, "wap_yield": None,
                    "volume": _num(g(row, "total traded volume")),
                    "turnover": turnover,
                    "num_trades": (int(_num(g(row, "number of trades"))
                                       or 0) or None),
                }
        elif low.startswith("icdm"):
            for row in rdr:
                isin = _isin(g(row, "isin"))
                if isin not in universe:
                    continue
                turnover = _num(g(row, "turnover"))
                yield {
                    "isin": isin, "exchange": "BSE", "segment": "otc",
                    "price_basis": "clean_pct",
                    "open": None, "high": None, "low": None, "close": None,
                    "prev_close": None,
                    "last_price": _num(g(row, "ltp")),
                    "wap": _num(g(row, "weighted average price")),
                    "last_yield": None,
                    "wap_yield": _num(g(row, "weighted average yield")),
                    "volume": None,
                    "turnover": (turnover * LAKH
                                 if turnover is not None else None),
                    "num_trades": None,
                }


PARSERS = {"nse_cm": parse_nse_cm, "nse_cbm": parse_nse_cbm,
           "bse_debt": parse_bse_debt}

COLS: list[LiteralString] = [
    "isin", "trade_date", "exchange", "segment", "price_basis",
        "open", "high", "low", "close", "prev_close", "last_price",
        "wap", "last_yield", "wap_yield", "volume", "turnover",
        "num_trades", "raw_id"]


def merge(a, b):
    """Same ISIN appearing twice in one file (e.g. two scrip codes):
    aggregate volumes/turnover, widen the range, keep first prices."""
    for k in ("volume", "turnover", "num_trades"):
        if b.get(k) is not None:
            a[k] = (a.get(k) or 0) + b[k]
    if b.get("high") is not None:
        a["high"] = max(a["high"] or b["high"], b["high"])
    if b.get("low") is not None:
        a["low"] = min(a["low"] or b["low"], b["low"])
    return a


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rebuild", action="store_true",
                    help="truncate eod_trades and re-parse every raw file")
    args = ap.parse_args()

    universe = set(db.universe_isins(listed_only=False))
    inserted = files = dupes = 0

    with db._conn() as conn:
        conn.execute(SCHEMA)
        where = "http_status = 200 AND content IS NOT NULL"
        if args.rebuild:
            conn.execute("TRUNCATE eod_trades")
            conn.execute("UPDATE eod_raw SET parsed_at = NULL")
        else:
            where += " AND parsed_at IS NULL"
        raws = conn.execute(
            f"SELECT id, source, trade_date, content FROM eod_raw "
            f"WHERE {where} ORDER BY trade_date, source").fetchall()

        errors = 0
        for raw in raws:
            files += 1
            content = gzip.decompress(raw["content"])
            day = raw["trade_date"]
            rows = {}
            try:
                for rec in PARSERS[raw["source"]](content, universe):
                    key = (rec["isin"], rec["exchange"], rec["segment"])
                    if key in rows:
                        rows[key] = merge(rows[key], rec)
                        dupes += 1
                    else:
                        rec["trade_date"] = day
                        rec["raw_id"] = raw["id"]
                        rows[key] = rec
            except Exception as exc:  # noqa: BLE001 - keep parsing
                errors += 1
                print(f"ERROR {raw['source']} {day}: "
                      f"{type(exc).__name__}: {str(exc)[:100]}", flush=True)
                continue  # parsed_at stays NULL -> retried after a fix
            if files % 500 == 0:
                print(f"[{files}] {day} rows so far={inserted}", flush=True)
            if rows:
                conn.cursor().executemany(
                    f"INSERT INTO eod_trades ({', '.join(COLS)}) VALUES "
                    f"({', '.join('%s' for _ in COLS)}) "
                    "ON CONFLICT (isin, trade_date, exchange, segment) "
                    f"DO UPDATE SET {UPSERT_SET}",
                    [tuple(r[c] for c in COLS) for r in rows.values()])
                inserted += len(rows)
            conn.execute(
                "UPDATE eod_raw SET parsed_at = now() WHERE id = %s",
                (raw["id"],))

    print(f"parsed {files} files -> {inserted} trade rows "
          f"({dupes} intra-file duplicates merged, {errors} file errors)")
    db.close_pool()


if __name__ == "__main__":
    main()
