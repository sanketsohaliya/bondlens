"""Best-ask YTM for a set of bonds, under the requesting user's own
Kite session (the explorer's "YTM @ Ask" column).

Same yield engine as everywhere else — schedule.build + exact_ytm —
just at the best (lowest) resting ask across the bond's NSE/BSE
listings instead of the last traded price. Ask data is cached per
(user, isin) for 60s, including "no ask" results, so a page load costs
at most ceil(n/500) Kite quote calls per user per minute.
"""
import time
from datetime import date
from typing import Any

from . import kite, kite_connect, queries
from .analytics import schedule, yields

MAX_ISINS = 1000
CACHE_TTL_SECONDS = 60
QUOTE_CHUNK = 500  # Kite /quote takes up to 500 instruments per call

# (user_id, isin) -> (fetched_at, entry-or-None); None = known no-ask.
_cache: dict[tuple[int, str], tuple[float, dict[str, Any] | None]] = {}


def _best_ask(quotes: dict[str, Any], keys: list[str]) -> tuple[float, float | None] | None:
    """Lowest first-level ask (price, qty) across a bond's listings."""
    best: tuple[float, float | None] | None = None
    for key in keys:
        quote = quotes.get(key) or {}
        sell = (quote.get("depth") or {}).get("sell") or []
        top = sell[0] if sell else {}
        price = top.get("price")
        if not price:
            continue
        if best is None or price < best[0]:
            qty = top.get("quantity")
            best = (float(price), float(qty) if qty else None)
    return best


def compute(user_id: int, isins: list[str]) -> list[dict[str, Any]]:
    """[{isin, ask_price, ask_qty, ytm}] for the bonds with a live ask."""
    isins = list(dict.fromkeys(isins))[:MAX_ISINS]
    now = time.time()
    out: list[dict[str, Any]] = []

    misses: list[str] = []
    for isin in isins:
        hit = _cache.get((user_id, isin))
        if hit and now - hit[0] < CACHE_TTL_SECONDS:
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
        quotes.update(kite_connect.fetch_quotes(user_id, instruments[i:i + QUOTE_CHUNK]))

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
        price, qty = ask
        sched = schedule.build(isin, flows.get(isin, []), terms.get(isin),
                               today=today)
        entry = {
            "isin": isin,
            "ask_price": price,
            "ask_qty": qty,
            "ytm": yields.exact_ytm(price, sched, today) if sched.usable else None,
        }
        _cache[(user_id, isin)] = (now, entry)
        out.append(entry)
    return out
