"""NSE listed-debt master (DEBT.csv from NSE archives).

Reconstructed from bytecode after the source was lost in the v2 cleanup —
only the functions the universe builder uses.
"""
import csv
import io
import re
from typing import Dict, List

import requests

BASE = "https://www.nseindia.com"
DEBT_MASTER_URL = "https://nsearchives.nseindia.com/content/equities/DEBT.csv"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/market-data/bonds-traded-in-capital-market",
}

_ISIN_RE = re.compile(r"^IN[A-Z0-9]{10}$")


def _to_float(value):
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def fetch_debt_csv(timeout: int = 20) -> str:
    """Raw DEBT.csv text from NSE archives (parsed below; also archived)."""
    resp = requests.get(DEBT_MASTER_URL, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def parse_debt_isins(text: str) -> List[Dict]:
    """Per-ISIN records from NSE's DEBT.csv master (the CM listed-debt set).

    Returns one dict per ISIN with symbol, series, face value, coupon
    (IP rate), listing date and maturity (redemption) date. The caller
    filters to corporate ISINs; this keeps every debt ISIN it finds.
    """
    reader = csv.reader(io.StringIO(text))
    header = [c.strip().upper() for c in next(reader, [])]

    def idx(name):
        try:
            return header.index(name)
        except ValueError:
            return None

    sym_i = idx("SYMBOL")
    name_i = idx("NAME OF COMPANY")
    ser_i = idx("SERIES")
    fv_i = idx("FACE VALUE")
    coup_i = idx("IP RATE")
    list_i = idx("DATE OF LISTING")
    red_i = idx("REDEMPTION DATE")

    def cell(row, i):
        return row[i].strip() if i is not None and len(row) > i else ""

    out = {}
    for row in reader:
        isin = next((c.strip() for c in reversed(row)
                     if _ISIN_RE.match(c.strip())), None)
        if not isin or isin in out:
            continue
        out[isin] = {
            "isin": isin,
            "nse_symbol": cell(row, sym_i) or None,
            "nse_series": cell(row, ser_i) or None,
            "issuer_name": cell(row, name_i) or None,
            "face_value": _to_float(cell(row, fv_i)),
            "coupon": _to_float(cell(row, coup_i)),
            "listing_date": cell(row, list_i) or None,
            "maturity_date": cell(row, red_i) or None,
        }
    return list(out.values())


def fetch_debt_isins(timeout: int = 20) -> List[Dict]:
    """Fetch and parse DEBT.csv in one step."""
    return parse_debt_isins(fetch_debt_csv(timeout=timeout))
