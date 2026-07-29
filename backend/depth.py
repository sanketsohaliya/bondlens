"""Live order-book depth proxied from the exchanges' quote APIs.

Neither exchange allows browser-side calls (CORS locked to their own
origins; NSE additionally needs a cookie handshake), so the API fetches
on demand — one call per drawer-tab open, absorbed by a short TTL cache.

BSE is the reliable source (plain JSON endpoint behind a Referer check).
NSE actively resists non-browser/datacenter traffic, so its fetcher is
best-effort: any failure degrades to None rather than erroring the
endpoint.
"""
import time
from datetime import UTC, datetime
from typing import Any

import requests

BSE_DEPTH_URL = "https://api.bseindia.com/BseIndiaAPI/api/MarketDepth/w"
BSE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://www.bseindia.com/",
    "Accept": "application/json, text/plain, */*",
}

NSE_BASE = "https://www.nseindia.com"
NSE_TRADE_INFO_URL = NSE_BASE + "/api/quote-equity"
NSE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE_BASE + "/market-data/bonds-traded-in-capital-market",
}

CACHE_TTL_SECONDS = 30
_cache: dict[str, tuple[float, dict]] = {}

# NSE cookies survive a few minutes; keep one primed session per process.
_nse_session: requests.Session | None = None
_nse_session_time = 0.0
_NSE_SESSION_TTL = 300


def _num(value: Any) -> float | None:
    """Exchange numbers arrive as strings with commas; '--' means empty."""
    if value in (None, "", "--", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _level(price: Any, qty: Any, orders: Any = None) -> dict | None:
    p, q = _num(price), _num(qty)
    if not p and not q:  # None or 0 both mean an empty slot
        return None
    return {"price": p, "qty": q, "orders": int(o) if (o := _num(orders)) else None}


def parse_bse_depth(payload: dict) -> dict | None:
    """BSE MarketDepth payload -> DepthSide dict (None when fully empty)."""
    bids = [lv for i in range(1, 6)
            if (lv := _level(payload.get(f"BPrice{i}"), payload.get(f"BQty{i}")))]
    asks = [lv for i in range(1, 6)
            if (lv := _level(payload.get(f"SPrice{i}"), payload.get(f"SQty{i}")))]
    if not bids and not asks:
        return None
    return {
        "bids": bids,
        "asks": asks,
        "total_bid_qty": _num(payload.get("TotalBQty")),
        "total_ask_qty": _num(payload.get("TotalSQty")),
        "as_of": payload.get("dttm") or None,
    }


def parse_nse_depth(payload: dict) -> dict | None:
    """NSE trade_info payload -> DepthSide dict (None when fully empty)."""
    book = payload.get("marketDeptOrderBook") or {}
    bids = [lv for row in (book.get("bid") or [])
            if (lv := _level(row.get("price"), row.get("quantity")))]
    asks = [lv for row in (book.get("ask") or [])
            if (lv := _level(row.get("price"), row.get("quantity")))]
    if not bids and not asks:
        return None
    trade_info = book.get("tradeInfo") or {}
    return {
        "bids": bids[:5],
        "asks": asks[:5],
        "total_bid_qty": _num(book.get("totalBuyQuantity")
                              or trade_info.get("totalBuyQuantity")),
        "total_ask_qty": _num(book.get("totalSellQuantity")
                              or trade_info.get("totalSellQuantity")),
        "as_of": None,
    }


def fetch_bse_depth(scrip_code: str | int) -> dict | None:
    try:
        resp = requests.get(
            BSE_DEPTH_URL, headers=BSE_HEADERS, timeout=10,
            params={"quotetype": "EQ", "scripcode": str(scrip_code), "flag": "0"})
        resp.raise_for_status()
        return parse_bse_depth(resp.json())
    except Exception:  # noqa: BLE001 - exchange being down must not 5xx us
        return None


def _nse_get_session() -> requests.Session:
    global _nse_session, _nse_session_time
    now = time.time()
    if _nse_session is None or now - _nse_session_time > _NSE_SESSION_TTL:
        s = requests.Session()
        s.headers.update(NSE_HEADERS)
        s.get(NSE_BASE, timeout=10)  # prime akamai cookies
        _nse_session = s
        _nse_session_time = now
    return _nse_session


def fetch_nse_depth(symbol: str, series: str | None) -> dict | None:
    try:
        s = _nse_get_session()
        params: dict[str, str] = {"symbol": symbol, "section": "trade_info"}
        if series:
            params["series"] = series
        resp = s.get(NSE_TRADE_INFO_URL, params=params, timeout=10)
        resp.raise_for_status()
        return parse_nse_depth(resp.json())
    except Exception:  # noqa: BLE001 - NSE blocking datacenter IPs is expected
        return None


def order_book(isin: str, nse_symbol: str | None, nse_series: str | None,
               bse_scrip_code: str | int | None) -> dict:
    """Depth for one bond across both exchanges, TTL-cached per ISIN."""
    now = time.time()
    hit = _cache.get(isin)
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    book = {
        "isin": isin,
        "fetched_at": datetime.now(UTC).isoformat(),
        "nse": fetch_nse_depth(nse_symbol, nse_series) if nse_symbol else None,
        "bse": fetch_bse_depth(bse_scrip_code) if bse_scrip_code else None,
        "ytm_at_ask": [],
    }
    _cache[isin] = (now, book)
    return book
