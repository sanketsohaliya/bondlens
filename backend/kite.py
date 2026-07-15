"""Map NSE/BSE bond symbols to Zerodha Kite instrument tokens.

Kite publishes public per-exchange instrument dumps (no auth) at
https://api.kite.trade/instruments/{NSE,BSE}. NSE bond trading symbols
there are formatted as ``{SYMBOL}-{SERIES}`` (e.g. ``96IIFL28-N5``);
BSE ones match the scrip id (e.g. ``962APSB32A``). With the
instrument_token, Kite web can deep-link to the instrument's chart page,
which has Buy/Sell buttons for logged-in users.
"""
import csv
import io
import time

import requests

INSTRUMENTS_URL = "https://api.kite.trade/instruments/{exchange}"
CACHE_TTL_SECONDS = 12 * 3600

_cache: dict[str, dict[str, int]] = {}
_cache_time: dict[str, float] = {}


def _load(exchange: str) -> dict[str, int]:
    resp = requests.get(INSTRUMENTS_URL.format(exchange=exchange), timeout=30)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    return {
        row["tradingsymbol"]: int(row["instrument_token"])
        for row in reader
        if row.get("tradingsymbol") and row.get("instrument_token")
    }


def _instruments(exchange: str) -> dict[str, int]:
    now = time.time()
    if not _cache.get(exchange) or now - _cache_time.get(exchange, 0.0) > CACHE_TTL_SECONDS:
        try:
            _cache[exchange] = _load(exchange)
            _cache_time[exchange] = now
        except Exception:  # noqa: BLE001 - Kite being down shouldn't break the app
            pass
    return _cache.get(exchange) or {}


def _chart_url(exchange: str, tradingsymbol: str, token: int) -> str:
    return f"https://kite.zerodha.com/markets/chart/web/tvc/{exchange}/{tradingsymbol}/{token}"


def _nse_url(nse_symbol: str | None, nse_series: str | None) -> str | None:
    if not nse_symbol:
        return None
    instruments = _instruments("NSE")
    tradingsymbol = f"{nse_symbol}-{nse_series}" if nse_series else nse_symbol
    token = instruments.get(tradingsymbol) or instruments.get(nse_symbol)
    if token is None:
        return None
    return _chart_url("NSE", tradingsymbol if tradingsymbol in instruments
                      else nse_symbol, token)


def _bse_url(bse_scrip_id: str | None) -> str | None:
    if not bse_scrip_id:
        return None
    token = _instruments("BSE").get(bse_scrip_id)
    return _chart_url("BSE", bse_scrip_id, token) if token is not None else None


def kite_url(nse_symbol: str | None, nse_series: str | None,
             bse_scrip_id: str | None = None,
             prefer: str | None = None) -> str | None:
    """Kite deep link for a bond. `prefer` (exchange of the last retail
    trade) decides which listing to link when the bond is on both; NSE
    otherwise."""
    nse = lambda: _nse_url(nse_symbol, nse_series)  # noqa: E731
    bse = lambda: _bse_url(bse_scrip_id)  # noqa: E731
    first, second = (bse, nse) if (prefer or "").upper() == "BSE" else (nse, bse)
    return first() or second()
