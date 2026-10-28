"""Best-ask YTM for a set of bonds, under the requesting user's own
Kite session (the explorer's "YTM @ Ask" column).

Same yield engine as everywhere else — schedule.build + exact_ytm —
just at the best (lowest) resting ask across the bond's NSE/BSE
listings instead of the last traded price.

Two entry points:
  compute(user, isins)   explicit ISINs (watchlists, tests, one-offs)
  compute_page(user, k)  page k of the WHOLE listed universe. The explorer
                         walks every page once per page load and then
                         filters client-side, so an ask can never be
                         missed because a cap or the active filters kept
                         its bond out of the request.

Ask data is a snapshot: what the book showed when we fetched it, with
its capture time. Nothing expires it; a reload captures again. Results
are cached per (user, isin) for 60s, including "no ask", so two tabs or
a quick reload do not double the Kite traffic. Kite allows one /quote
call per second, so chunks are paced.
"""
import math
import threading
import time
from datetime import UTC, date, datetime
from typing import Any

from . import kite, kite_connect, queries, quote_time, schedules
from .analytics import schedule, valuation

MAX_ISINS = 1000
PAGE = 1000
CACHE_TTL_SECONDS = 60
UNIVERSE_TTL_SECONDS = 600
QUOTE_CHUNK = 500  # Kite /quote takes up to 500 instruments per call
QUOTE_MIN_INTERVAL = 1.05  # Kite rate limit: 1 quote request per second

# (user_id, isin) -> (fetched_at, entry-or-None); None = known no-ask.
_cache: dict[tuple[int, str], tuple[float, dict[str, Any] | None]] = {}
_universe: tuple[float, list[str]] | None = None
_quote_lock = threading.Lock()
_last_quote_call = 0.0


def _best_ask(quotes: dict[str, Any], keys: list[str]) -> dict | None:
    """Lowest positive-size ask across a bond's listings. A book that has
    not changed since the open is still a live book (see quote_time);
    only a future-dated source timestamp is refused."""
    best = None
    for key in keys:
        quote = quotes.get(key) or {}
        timing = quote_time.metadata(quote.get("_captured_at") or datetime.now(UTC), quote.get("timestamp"))
        if any(w["code"] == "future_quote_time" for w in timing["warnings"]):
            continue
        for top in (quote.get("depth") or {}).get("sell") or []:
            try:
                price, qty = float(top.get("price") or 0), float(top.get("quantity") or 0)
            except (ValueError, TypeError):
                continue
            if not (math.isfinite(price) and math.isfinite(qty) and price > 0 and qty > 0):
                continue
            if best is None or price < best["ask_price"]:
                best = {"ask_price": price, "ask_qty": qty, "exchange": key.split(":", 1)[0], **timing}
    return best


def ask_prices(sides) -> list[float]:
    prices = set()
    for side in sides:
        for level in (side or {}).get("asks", []):
            try:
                price, qty = float(level.get("price") or 0), float(level.get("qty") or 0)
            except (ValueError, TypeError):
                continue
            if math.isfinite(price) and math.isfinite(qty) and price > 0 and qty > 0:
                prices.add(price)
    return sorted(prices)


class PriceCalculator:
    def __init__(self, isin: str):
        self.isin = isin
        self.as_of: date | None = None
        self.loaded_at = 0.0
        self.version: str | None = None
        self.sched: schedule.Schedule | None = None
        self.terms: dict | None = None
        self.core: dict = {}
        self.memo: dict[float, dict] = {}

    def values(self, prices: list[float], today: date | None = None) -> list[dict]:
        if not prices:
            return []
        today = today or date.today()
        if (self.as_of != today or self.version != valuation.VERSION
                or time.monotonic() - self.loaded_at >= CACHE_TTL_SECONDS):
            self.terms = queries.bond_terms(self.isin)
            self.sched = schedules.build_for(self.isin, today)
            self.core = queries.analytics_row(self.isin, 7) or {}
            self.as_of, self.version, self.loaded_at = today, valuation.VERSION, time.monotonic()
            self.memo.clear()
        assert self.sched is not None
        for price in prices:
            if price not in self.memo:
                self.memo[price] = valuation.calculate(price, self.sched, self.terms, today,
                                                       otc_yield=self.core.get("otc_yield"),
                                                       otc_trade_date=self.core.get("otc_trade_date"))
        return [self.memo[p] for p in prices]


def _paced_quotes(user_id: int, instruments: list[str]) -> dict[str, Any]:
    """fetch_quotes with >= QUOTE_MIN_INTERVAL between calls, process-wide."""
    global _last_quote_call
    with _quote_lock:
        wait = _last_quote_call + QUOTE_MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            return kite_connect.fetch_quotes(user_id, instruments)
        finally:
            _last_quote_call = time.monotonic()


def universe_pages() -> list[list[str]]:
    """The listed universe cut into PAGE-sized pages (cached ~10 min; the
    universe itself changes once a day at 05:30)."""
    global _universe
    now = time.monotonic()
    if _universe is None or now - _universe[0] > UNIVERSE_TTL_SECONDS:
        _universe = (now, queries.ask_universe())
    isins = _universe[1]
    return [isins[i:i + PAGE] for i in range(0, len(isins), PAGE)] or [[]]


def compute_page(user_id: int, page: int) -> dict[str, Any]:
    pages = universe_pages()
    isins = pages[page] if 0 <= page < len(pages) else []
    return {"items": compute(user_id, isins), "page": page, "pages": len(pages), "isins": isins}


def compute(user_id: int, isins: list[str]) -> list[dict[str, Any]]:
    """[{isin, ask_price, ask_qty, ytm}] for the bonds with a live ask."""
    isins = list(dict.fromkeys(isins))[:MAX_ISINS]
    now = time.time()
    today = date.today()
    for key, (ts, _) in list(_cache.items()):
        if not 0 <= now - ts < CACHE_TTL_SECONDS:
            _cache.pop(key, None)
    out: list[dict[str, Any]] = []

    misses: list[str] = []
    for isin in isins:
        hit = _cache.get((user_id, isin))
        same_day = hit and datetime.fromtimestamp(hit[0], quote_time.IST).date() == today
        current = hit and (hit[1] is None or (hit[1].get("as_of") == today.isoformat()
                                              and hit[1].get("calculation_version") == valuation.VERSION))
        if hit and same_day and current and 0 <= now - hit[0] < CACHE_TTL_SECONDS:
            if hit[1] is not None:
                out.append(hit[1])
        else:
            misses.append(isin)
    if not misses:
        return out

    # Resolve Kite tradingsymbols for both listings of every miss.
    symbols = queries.bonds_symbols(misses)
    keys_by_isin: dict[str, list[str]] = {}
    for isin in misses:
        row = symbols.get(isin) or {}
        keys = []
        if nse := kite.resolve_nse(row.get("nse_symbol"), row.get("nse_series")):
            keys.append(f"NSE:{nse[0]}")
        if bse := kite.resolve_bse(row.get("bse_scrip_id")):
            keys.append(f"BSE:{bse[0]}")
        keys_by_isin[isin] = keys

    instruments = [k for keys in keys_by_isin.values() for k in keys]
    quotes: dict[str, Any] = {}
    for i in range(0, len(instruments), QUOTE_CHUNK):
        batch = _paced_quotes(user_id, instruments[i:i + QUOTE_CHUNK])
        captured = datetime.now(UTC).isoformat()
        quotes.update({key: {**value, "_captured_at": captured} for key, value in batch.items() if isinstance(value, dict)})

    asked = {isin: ask for isin in misses
             if (ask := _best_ask(quotes, keys_by_isin[isin]))}

    # One bulk load of schedule inputs for everything with an ask.
    today = date.today()
    flows = queries.cashflows_for(list(asked)) if asked else {}
    terms = queries.terms_for(list(asked)) if asked else {}
    notices = queries.exchange_notice_rows(list(asked)) if asked else {}

    for isin in misses:
        ask = asked.get(isin)
        if ask is None:
            _cache[(user_id, isin)] = (now, None)
            continue
        sched = schedules.build(isin, flows.get(isin, []), terms.get(isin), today, notices.get(isin))
        source = symbols.get(isin) or {}
        priced = valuation.calculate(ask["ask_price"], sched, terms.get(isin), today,
                                     otc_yield=float(source["otc_yield"]) if source.get("otc_yield") is not None else None,
                                     otc_trade_date=source.get("otc_trade_date"))
        entry = {**priced, **ask, "isin": isin, "warnings": priced["warnings"] + ask["warnings"]}
        _cache[(user_id, isin)] = (now, entry)
        out.append(entry)
    return out
