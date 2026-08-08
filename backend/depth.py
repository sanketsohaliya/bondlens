"""Live order-book depth: Kite Connect when the user has connected
their Zerodha account, exchange quote APIs otherwise.

Kite's /quote endpoint returns both listings' 5-level depth (with order
counts) in one authenticated call — the primary source for connected
users, cached per (isin, user) so one user's feed is never served to
another.

The exchange scrapers remain the fallback: neither exchange allows
browser-side calls (CORS locked to their own origins; NSE additionally
needs a cookie handshake), so the API fetches on demand — one call per
drawer-tab open, absorbed by a short TTL cache. BSE is the reliable
source (plain JSON endpoint behind a Referer check). NSE actively
resists non-browser/datacenter traffic, so its fetcher is best-effort:
any failure degrades to None rather than erroring the endpoint.
"""
import time
from datetime import UTC, datetime
from typing import Any

import requests

from . import kite, kite_connect

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
        "source": "exchange",
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
        "source": "exchange",
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


def fetch_kite_depth(user_id: int, nse_symbol: str | None,
                     nse_series: str | None, bse_scrip_id: str | None,
                     ) -> tuple[dict | None, dict | None]:
    """(nse_side, bse_side) from one Kite /quote call under the user's
    token; (None, None) when Kite can't serve (per-exchange fallback
    handled by the caller)."""
    nse_hit = kite.resolve_nse(nse_symbol, nse_series)
    bse_hit = kite.resolve_bse(bse_scrip_id)
    keys = {}
    if nse_hit:
        keys["nse"] = f"NSE:{nse_hit[0]}"
    if bse_hit:
        keys["bse"] = f"BSE:{bse_hit[0]}"
    if not keys:
        return None, None
    quotes = kite_connect.fetch_quotes(user_id, list(keys.values()))

    def side(name: str) -> dict | None:
        quote = quotes.get(keys[name]) if name in keys else None
        return kite_connect.parse_kite_depth(quote) if quote else None

    return side("nse"), side("bse")


def order_book(isin: str, nse_symbol: str | None, nse_series: str | None,
               bse_scrip_code: str | int | None,
               bse_scrip_id: str | None = None,
               user_id: int | None = None) -> dict:
    """Depth for one bond across both exchanges, TTL-cached.

    Kite (the user's own token) is the primary source; each exchange
    falls back to its scraper when Kite can't serve it. Kite-sourced
    books are cached per (isin, user) so they never cross users;
    scraper-only books share the public per-ISIN cache entry."""
    now = time.time()
    use_kite = (user_id is not None and kite_connect.configured()
                and kite_connect.token_for(user_id) is not None)
    cache_key = f"{isin}:{user_id}" if use_kite else f"{isin}:pub"
    hit = _cache.get(cache_key)
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]

    nse_side = bse_side = None
    if use_kite:
        assert user_id is not None
        nse_side, bse_side = fetch_kite_depth(user_id, nse_symbol,
                                              nse_series, bse_scrip_id)
    if nse_side is None and nse_symbol:
        nse_side = fetch_nse_depth(nse_symbol, nse_series)
    if bse_side is None and bse_scrip_code:
        bse_side = fetch_bse_depth(bse_scrip_code)

    book = {
        "isin": isin,
        "fetched_at": datetime.now(UTC).isoformat(),
        "nse": nse_side,
        "bse": bse_side,
        "ytm_at_ask": [],
    }
    _cache[cache_key] = (now, book)
    return book
