"""Best-ask YTM for a set of bonds, under the requesting user's own
Kite session (the explorer's "YTM @ Ask" column).

Same yield engine as everywhere else — schedule.build + exact_ytm —
just at the best (lowest) resting ask across the bond's NSE/BSE
listings instead of the last traded price. Ask data is cached per
(user, isin) for 60s, including "no ask" results, so a page load costs
at most ceil(n/500) Kite quote calls per user per minute.
"""
import math
import time
from datetime import UTC, date, datetime
from typing import Any

from . import kite, kite_connect, queries, quote_time
from .analytics import schedule, valuation

MAX_ISINS = 1000
CACHE_TTL_SECONDS = 60
QUOTE_CHUNK = 500  # Kite /quote takes up to 500 instruments per call

# (user_id, isin) -> (fetched_at, entry-or-None); None = known no-ask.
_cache: dict[tuple[int, str], tuple[float, dict[str, Any] | None]] = {}


def _best_ask(quotes: dict[str, Any], keys: list[str]) -> dict | None:
    """Lowest positive-size, non-expired ask across a bond's listings."""
    best = None
    for key in keys:
        quote = quotes.get(key) or {}
        timing = quote_time.metadata(quote.get("_captured_at") or datetime.now(UTC), quote.get("timestamp"))
        if any(w["code"] in {"stale_quote", "future_quote_time"} for w in timing["warnings"]):
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
            self.sched = schedule.build(self.isin, queries.bond_cashflows(self.isin), self.terms, today=today)
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
        expiry = quote_time.timestamp(hit[1].get("expires_at")) if hit and hit[1] else None
        quote_current = hit and (hit[1] is None or (expiry is not None and expiry > datetime.now(UTC)))
        if hit and same_day and current and quote_current and 0 <= now - hit[0] < CACHE_TTL_SECONDS:
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
        batch = kite_connect.fetch_quotes(user_id, instruments[i:i + QUOTE_CHUNK])
        captured = datetime.now(UTC).isoformat()
        quotes.update({key: {**value, "_captured_at": captured} for key, value in batch.items() if isinstance(value, dict)})

    asked = {isin: ask for isin in misses
             if (ask := _best_ask(quotes, keys_by_isin[isin]))}

    # One bulk load of schedule inputs for everything with an ask.
    today = date.today()
    flows = queries.cashflows_for(list(asked)) if asked else {}
    terms = queries.terms_for(list(asked)) if asked else {}

    for isin in misses:
        ask = asked.get(isin)
        if ask is None:
            _cache[(user_id, isin)] = (now, None)
            continue
        sched = schedule.build(isin, flows.get(isin, []), terms.get(isin), today=today)
        source = symbols.get(isin) or {}
        priced = valuation.calculate(ask["ask_price"], sched, terms.get(isin), today,
                                     otc_yield=float(source["otc_yield"]) if source.get("otc_yield") is not None else None,
                                     otc_trade_date=source.get("otc_trade_date"))
        entry = {**priced, **ask, "isin": isin, "warnings": priced["warnings"] + ask["warnings"]}
        _cache[(user_id, isin)] = (now, entry)
        out.append(entry)
    return out
