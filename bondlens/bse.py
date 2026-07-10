"""Fetch BSE end-of-day bond prices from the daily bhavcopy CSV.

BSE's website is an Angular SPA whose data APIs are awkward to discover and
rate-limited, but the exchange publishes a daily "common format" bhavcopy —
a plain CSV covering every instrument traded on the capital-market segment,
including F-group (debt) series:
https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_YYYYMMDD_F_0000.CSV

Corporate bonds often trade on BSE's retail debt segment on days they don't
trade on NSE, so this fills staleness gaps. EOD only — no intraday quotes.
"""
import csv
import io
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import requests

BHAVCOPY_URL = (
    "https://www.bseindia.com/download/BhavCopy/Equity/"
    "BhavCopy_BSE_CM_0_0_0_{day}_F_0000.CSV"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Referer": "https://www.bseindia.com/",
}

# Bhavcopy exists only for trading days; walk back at most this many days.
MAX_LOOKBACK_DAYS = 7


def _to_float(value) -> Optional[float]:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def parse_bhavcopy(text: str) -> List[Dict]:
    """Normalize bhavcopy rows to one quote per debt-looking ISIN.

    Keeps every row with an ISIN — the caller joins against its own bond
    universe, which is a cleaner filter than second-guessing BSE's series
    codes (F-group also contains a few ETFs).
    """
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        isin = (row.get("ISIN") or "").strip()
        if not isin:
            continue
        out.append({
            "isin": isin,
            "symbol": (row.get("TckrSymb") or "").strip() or None,
            "series": (row.get("SctySrs") or "").strip() or None,
            "trade_date": (row.get("TradDt") or "").strip() or None,
            "ltp": _to_float(row.get("LastPric")),
            "close": _to_float(row.get("ClsPric")),
            "open": _to_float(row.get("OpnPric")),
            "high": _to_float(row.get("HghPric")),
            "low": _to_float(row.get("LwPric")),
            "qty": _to_float(row.get("TtlTradgVol")),
            "traded_value": _to_float(row.get("TtlTrfVal")),
            "num_trades": _to_float(row.get("TtlNbOfTxsExctd")),
        })
    return out


def fetch_bhavcopy(timeout: int = 60) -> Tuple[Optional[str], List[Dict]]:
    """Fetch the most recent bhavcopy, walking back over non-trading days.

    Returns (trade_date_iso, quotes). Raises only if every day in the
    lookback window fails.
    """
    last_error: Optional[Exception] = None
    for back in range(MAX_LOOKBACK_DAYS):
        day = date.today() - timedelta(days=back)
        url = BHAVCOPY_URL.format(day=day.strftime("%Y%m%d"))
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            continue
        # Missing files come back as an HTML error page with status 200.
        if not resp.ok or resp.text.lstrip().startswith("<"):
            continue
        quotes = parse_bhavcopy(resp.text)
        if quotes:
            return day.isoformat(), quotes
    if last_error:
        raise last_error
    raise RuntimeError(
        f"No BSE bhavcopy found in the last {MAX_LOOKBACK_DAYS} days")
