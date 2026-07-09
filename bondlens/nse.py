"""Fetch live corporate bond data from NSE's website JSON endpoint.

NSE requires a browser-like session: hit the homepage first to obtain
cookies, then call the API with matching headers.
"""
import csv
import io
from typing import Dict, List

import requests

BASE_URL = "https://www.nseindia.com"
BONDS_URL = f"{BASE_URL}/api/liveBonds-traded-on-cm?type=bonds"
DEBT_MASTER_URL = "https://nsearchives.nseindia.com/content/equities/DEBT.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE_URL}/market-data/bonds-traded-in-capital-market",
}


def fetch_bonds(timeout: int = 15) -> dict:
    """Return the raw JSON payload of bonds traded on NSE capital market."""
    session = requests.Session()
    session.headers.update(HEADERS)
    # Cookie handshake
    session.get(BASE_URL, timeout=timeout)
    resp = session.get(BONDS_URL, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_debt_master(timeout: int = 20) -> List[Dict]:
    """All listed debt series from NSE's master CSV: symbol, company name,
    series, face value, coupon (IP rate) and redemption (maturity) date."""
    resp = requests.get(DEBT_MASTER_URL, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    reader = csv.reader(io.StringIO(resp.text))
    cols = [c.strip().upper() for c in next(reader, [])]

    def idx(name):
        try:
            return cols.index(name)
        except ValueError:
            return None

    sym_i, name_i = idx("SYMBOL"), idx("NAME OF COMPANY")
    if sym_i is None or name_i is None:
        return []
    ser_i, fv_i = idx("SERIES"), idx("FACE VALUE")
    coup_i, red_i = idx("IP RATE"), idx("REDEMPTION DATE")

    def cell(row, i):
        return row[i].strip() if i is not None and len(row) > i else ""

    records = []
    for row in reader:
        sym, name = cell(row, sym_i), cell(row, name_i)
        if not sym or not name:
            continue
        records.append({
            "symbol": sym,
            "name": name,
            "series": cell(row, ser_i) or None,
            "face_value": _to_float(cell(row, fv_i)),
            "coupon": _to_float(cell(row, coup_i)),
            "maturity_date": cell(row, red_i) or None,
        })
    return records


def _to_float(value):
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def normalize(payload: dict) -> List[Dict]:
    """Flatten NSE payload into clean records."""
    records = []
    for row in payload.get("data", []):
        records.append(
            {
                "isin": row.get("isin"),
                "symbol": row.get("symbol"),
                "series": row.get("series"),
                "coupon": _to_float(row.get("coupr")),
                "face_value": _to_float(row.get("face_value")),
                "ltp": _to_float(row.get("ltP")),
                "open": _to_float(row.get("open")),
                "high": _to_float(row.get("high")),
                "low": _to_float(row.get("low")),
                "close": _to_float(row.get("close")),
                "pct_change": _to_float(row.get("per")),
                "qty": _to_float(row.get("qty")),
                "traded_value": _to_float(row.get("trdVal")),
                "maturity_date": row.get("maturity_date"),
                "next_interest_date": row.get("nxtip_date"),
                "credit_rating": row.get("credit_rating"),
                "rating_agency": row.get("rating_agency"),
            }
        )
    return records
