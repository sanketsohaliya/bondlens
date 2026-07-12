"""Map NSE bond symbols to Zerodha Kite instrument tokens.

Kite publishes a public instruments dump (no auth) at
https://api.kite.trade/instruments/NSE. Bond trading symbols there are
formatted as ``{SYMBOL}-{SERIES}`` (e.g. ``96IIFL28-N5``). With the
instrument_token, Kite web can deep-link to the instrument's chart page,
which has Buy/Sell buttons for logged-in users.
"""
import csv
import io
import time

import requests

INSTRUMENTS_URL = "https://api.kite.trade/instruments/NSE"
CACHE_TTL_SECONDS = 12 * 3600

_cache: dict[str, int] = {}
_cache_time: float = 0.0


def _load() -> dict[str, int]:
    resp = requests.get(INSTRUMENTS_URL, timeout=30)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    return {
        row["tradingsymbol"]: int(row["instrument_token"])
        for row in reader
        if row.get("tradingsymbol") and row.get("instrument_token")
    }


def token_for(symbol: str | None, series: str | None) -> int | None:
    """Return Kite instrument_token for an NSE bond symbol+series, or None."""
    global _cache, _cache_time
    if not symbol:
        return None
    now = time.time()
    if not _cache or now - _cache_time > CACHE_TTL_SECONDS:
        try:
            _cache = _load()
            _cache_time = now
        except Exception:  # noqa: BLE001 - Kite being down shouldn't break the app
            if not _cache:
                return None
    tradingsymbol = f"{symbol}-{series}" if series else symbol
    return _cache.get(tradingsymbol) or _cache.get(symbol)


def kite_url(symbol: str | None, series: str | None) -> str | None:
    token = token_for(symbol, series)
    if token is None:
        return None
    tradingsymbol = f"{symbol}-{series}" if series else symbol
    return f"https://kite.zerodha.com/markets/chart/web/tvc/NSE/{tradingsymbol}/{token}"
