"""Opportunity alerts: the pure parts.

A rule is a saved Explore screen (static filters that pick the candidate
bonds) plus ask-side conditions; an exit target is a per-holding bid
condition. The watcher must decide on every depth tick in microseconds,
so yields are turned into prices ahead of time: "YTM @ ask >= 12%" is,
for one bond on one valuation date, exactly "ask price <= P*". Those
P* values live in a cache (threshold_cache); this module solves for them
and does the comparisons. Nothing here touches the network or the DB.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .analytics import schedule, yields

IST = ZoneInfo("Asia/Kolkata")

# Explore filter keys a rule may carry. They describe the bond, not its
# trading history: a never-traded bond with a great resting ask is the
# very thing an alert exists for, so min_ytm / turnover / traded_within /
# fresh_only / below_par / max_ltp / retail_only / otc_only are refused.
STATIC_FILTER_KEYS: frozenset[str] = frozenset({
    "min_rating", "rating", "min_years", "max_years", "sector", "industry",
    "issuer", "exchange", "frequency", "payout", "secured_only",
    "unsecured_only", "tax_free_only", "downgraded_only", "exact_only",
})
TRADE_HISTORY_KEYS: frozenset[str] = frozenset({
    "min_ytm", "max_ytm", "min_turnover", "min_otc_turnover", "traded_within",
    "fresh_only", "below_par", "max_ltp", "retail_only", "otc_only",
})

# Re-alert policy within one trading day.
REALERT_YTM_PTS = 0.25     # ask: yield must improve by this much
REALERT_BID_PCT = 0.5      # bid: price must improve by this much (percent)

PRICE_TOLERANCE = 0.01     # bisection stops within one paisa


def static_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only the filter keys a rule may use, dropping empties."""
    return {k: v for k, v in (filters or {}).items()
            if k in STATIC_FILTER_KEYS and v not in (None, "", False)}


def rejected_filters(filters: dict[str, Any] | None) -> list[str]:
    """Trade-history keys present in `filters` (for the API's 400 and the UI)."""
    return sorted(k for k, v in (filters or {}).items()
                  if k in TRADE_HISTORY_KEYS and v not in (None, "", False))


def schedule_fingerprint(terms: dict | None, cashflows: list[dict]) -> str:
    """Cheap hash of a bond's raw pricing inputs; a change invalidates its
    cached thresholds. Hashing inputs avoids building the schedule."""
    payload = {"terms": terms or {}, "flows": cashflows}
    return hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()[:20]


def _yield_at(price: float, sched: schedule.Schedule, terms: dict | None, today: date,
              calls: list[date]) -> float | None:
    perpetual = sched.perpetual or sched.shape == "perpetual"
    if perpetual:
        return yields.yield_to_call(price, sched, calls, today)
    return yields.exact_ytm(price, sched, today)


def price_for_yield(sched: schedule.Schedule, terms: dict | None, ytm: float,
                    today: date) -> float | None:
    """Dirty price at which the bond yields `ytm` % (the same engine the
    explorer uses, run backwards by bisection). Yield falls as price rises,
    so "yield >= ytm" is "price <= result". None when the schedule cannot
    be priced or no price in (0, 3 x face] reaches that yield."""
    # Only yields the engine itself would report can be inverted, and never
    # for a schedule that contradicts the stated maturity (see valuation).
    if not math.isfinite(ytm) or yields._plausible(ytm) is None:
        return None
    if schedule.maturity_mismatch(sched, terms):
        return None
    face = sched.face_value or 1000.0
    floor, ceiling = max(face * 0.01, 1.0), face * 3.0
    calls = yields.parse_call_dates(terms)

    def y(price: float) -> float | None:
        return _yield_at(price, sched, terms, today, calls)

    # The solver answers None outside its plausible yield band, so anchor
    # near par first; a bond with no yield anywhere near par is unpriceable.
    anchor = None
    for k in (1.0, 0.9, 1.1, 0.8, 1.2, 0.6, 1.5):
        p = face * k
        if (yk := y(p)) is not None:
            anchor = (p, yk)
            break
    if anchor is None:
        return None
    p0, y0 = anchor
    # Yield falls as price rises. Walk from the anchor to bracket the target:
    # lo has yield >= target (or None = implausibly high), hi has yield <
    # target (or None = implausibly low).
    if y0 >= ytm:
        lo, hi = p0, p0 * 1.15
        while hi < ceiling and (yh := y(hi)) is not None and yh >= ytm:
            lo, hi = hi, hi * 1.15
        if hi >= ceiling and (yh := y(ceiling)) is not None and yh >= ytm:
            return None  # yield stays above target up to 3x face: not meaningful
    else:
        lo, hi = p0 / 1.15, p0
        while lo > floor and (yl := y(lo)) is not None and yl < ytm:
            lo, hi = lo / 1.15, lo
        if lo <= floor and (yl := y(floor)) is not None and yl < ytm:
            return None  # even a near-zero price does not reach the target
    for _ in range(80):
        mid = (lo + hi) / 2
        ym = y(mid)
        above = ym >= ytm if ym is not None else mid < p0
        if above:
            lo = mid
        else:
            hi = mid
        if hi - lo <= PRICE_TOLERANCE:
            break
    return round(lo, 2)


# --- The rule's one label -------------------------------------------------------

def _inr_short(v: float) -> str:
    """₹95,000 / ₹2 L / ₹2.5 L / ₹1.2 Cr - the explorer's own shorthand."""
    if v >= 1e7:
        n = v / 1e7
        return f"₹{n:.1f} Cr".replace(".0 Cr", " Cr")
    if v >= 1e5:
        n = v / 1e5
        return f"₹{n:.1f} L".replace(".0 L", " L")
    return f"₹{v:,.0f}"


def _tenor(years: float) -> str:
    if years < 1:
        return f"{round(years * 12)}mo"
    text = f"{years:.1f}".rstrip("0").rstrip(".")
    return f"{text}y"


def rule_label(rule: dict[str, Any]) -> str:
    """The single human label for a rule, used verbatim everywhere: ask
    conditions first, then the filters in the bar's order, every part a
    'Field op value'. Never truncated - tight surfaces put it on its own line."""
    parts: list[str] = []
    if (v := _f(rule.get("min_ask_ytm"))) is not None:
        parts.append(f"Ask YTM ≥ {v:g}%")
    if (v := _f(rule.get("min_ticket"))) is not None:
        parts.append(f"Ticket ≥ {_inr_short(v)}")
    f = rule.get("filters") or {}
    if f.get("min_rating"):
        parts.append(f"Rating ≥ {str(f['min_rating']).upper()}")
    if f.get("rating"):
        parts.append(f"Rating ~ {f['rating']}")
    lo, hi = _f(f.get("min_years")), _f(f.get("max_years"))
    if lo is not None and hi is not None:
        parts.append(f"Maturity {_tenor(lo)}–{_tenor(hi)}")
    elif lo is not None:
        parts.append(f"Maturity ≥ {_tenor(lo)}")
    elif hi is not None:
        parts.append(f"Maturity ≤ {_tenor(hi)}")
    for key, label in (("sector", "Sector"), ("industry", "Industry"), ("payout", "Payout"),
                       ("frequency", "Frequency"), ("exchange", "Exchange")):
        if f.get(key):
            parts.append(f"{label}: {f[key]}")
    if f.get("issuer"):
        parts.append(f"Issuer ~ {f['issuer']}")
    if f.get("secured_only"):
        parts.append("Secured")
    if f.get("unsecured_only"):
        parts.append("Unsecured")
    if f.get("tax_free_only"):
        parts.append("Tax-free")
    if f.get("downgraded_only"):
        parts.append("Downgraded recently")
    return ", ".join(parts) if parts else "Any ask"


def target_label(target: dict[str, Any]) -> str:
    parts = []
    if (v := _f(target.get("min_bid_price"))) is not None:
        parts.append(f"Bid ≥ {_inr_short(v)}")
    if (v := _f(target.get("max_bid_ytm"))) is not None:
        parts.append(f"Bid YTM ≤ {v:g}%")
    return "Exit target: " + ", ".join(parts) if parts else "Exit target"


# --- Thresholds ---------------------------------------------------------------

@dataclass(frozen=True)
class AskRule:
    rule_id: int
    label: str
    max_ask_price: float | None   # from min_ask_ytm via the cache; None = unpriceable
    min_ticket: float | None      # INR on offer (price x qty) must be at least this
    min_ask_ytm: float | None = None   # the rule's own yield, for the post-push recheck
    # Auto-buy: armed while auto_buy_until is in the future.
    buy_budget: float | None = None
    buy_daily_cap: float | None = None
    auto_buy_until: datetime | None = None

    def armed(self, now: datetime) -> bool:
        return self.auto_buy_until is not None and now < self.auto_buy_until


@dataclass(frozen=True)
class BidTarget:
    min_bid_price: float | None   # max(min_bid_price, price for max_bid_ytm)
    max_bid_ytm: float | None = None
    label: str = "Exit target"


@dataclass
class Thresholds:
    """Per-ISIN conditions, ready for float comparisons on the tick path."""
    asks: dict[str, list[AskRule]] = field(default_factory=dict)
    bids: dict[str, BidTarget] = field(default_factory=dict)

    @property
    def isins(self) -> set[str]:
        return set(self.asks) | set(self.bids)


def build_thresholds(rules: list[dict], candidates: dict[int, list[str]],
                     targets: list[dict], prices: dict[tuple[str, float], float | None],
                     ) -> tuple[Thresholds, set[tuple[str, float]]]:
    """Assemble comparison thresholds from rules, their candidate ISINs,
    exit targets and the yield->price cache. Returns the thresholds plus
    the (isin, ytm) pairs the cache does not know yet (to be solved).

    A rule whose price is not cached yet is held back for that bond (no
    false positives on a half-built table); a bond cached as None is
    unpriceable and skipped."""
    th = Thresholds()
    missing: set[tuple[str, float]] = set()
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        min_ytm = _f(rule.get("min_ask_ytm"))
        if min_ytm is None:
            continue
        for isin in candidates.get(rule["id"], []):
            key = (isin, float(min_ytm))
            if key not in prices:
                missing.add(key)
                continue
            max_ask_price = prices[key]
            if max_ask_price is None:
                continue
            th.asks.setdefault(isin, []).append(AskRule(
                rule_id=rule["id"], label=rule_label(rule), max_ask_price=max_ask_price,
                min_ticket=_f(rule.get("min_ticket")), min_ask_ytm=min_ytm,
                buy_budget=_f(rule.get("buy_budget")), buy_daily_cap=_f(rule.get("buy_daily_cap")),
                auto_buy_until=rule.get("auto_buy_until")))
    for target in targets:
        if not target.get("enabled", True):
            continue
        isin = target["isin"]
        floor = _f(target.get("min_bid_price"))
        max_ytm = target.get("max_bid_ytm")
        if max_ytm is not None:
            key = (isin, float(max_ytm))
            if key not in prices:
                missing.add(key)
                continue
            by_yield = prices[key]
            if by_yield is not None:
                floor = by_yield if floor is None else max(floor, by_yield)
        if floor is not None:
            th.bids[isin] = BidTarget(min_bid_price=floor, max_bid_ytm=_f(max_ytm),
                                      label=target_label(target))
    return th, missing


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


# --- Tick evaluation ------------------------------------------------------------

def best_ask(levels: list[dict]) -> tuple[float, float] | None:
    """Lowest positive-size ask as (price, qty)."""
    best = None
    for level in levels or []:
        p, q = _f(level.get("price")), _f(level.get("qty"))
        if p and q and p > 0 and q > 0 and (best is None or p < best[0]):
            best = (p, q)
    return best


def best_bid(levels: list[dict]) -> tuple[float, float] | None:
    """Highest positive-size bid as (price, qty)."""
    best = None
    for level in levels or []:
        p, q = _f(level.get("price")), _f(level.get("qty"))
        if p and q and p > 0 and q > 0 and (best is None or p > best[0]):
            best = (p, q)
    return best


def match_ask(rules: list[AskRule], price: float, qty: float) -> list[AskRule]:
    """Rules satisfied by a best ask of `price` x `qty`."""
    out = []
    for r in rules:
        if r.max_ask_price is None or price > r.max_ask_price:
            continue
        if r.min_ticket is not None and price * qty < r.min_ticket:
            continue
        out.append(r)
    return out


def match_bid(target: BidTarget | None, price: float) -> bool:
    return target is not None and target.min_bid_price is not None and price >= target.min_bid_price


@dataclass(frozen=True)
class Hit:
    source: str          # 'rule:<id>' | 'target'
    isin: str
    side: str            # 'ask' | 'bid'
    price: float
    qty: float
    exchange: str | None
    rule_label: str | None = None
    ytm: float | None = None
    # The rule's yield bound (min for asks, max for bids), for the recheck.
    ytm_bound: float | None = None


def shortfall(hit: Hit) -> float | None:
    """How far the exact yield misses the rule's bound (None = clears it or
    unknown). A basis point of tolerance absorbs the solver's paisa rounding."""
    if hit.ytm is None or hit.ytm_bound is None:
        return None
    gap = (hit.ytm_bound - hit.ytm) if hit.side == "ask" else (hit.ytm - hit.ytm_bound)
    return round(gap, 2) if gap > 0.01 else None


def should_fire(previous: Hit | None, hit: Hit) -> bool:
    """Once per source/bond/day, again only when the quote got materially
    better: a cheaper ask (lower price) by enough to move the yield, or a
    higher bid by REALERT_BID_PCT. Yield improvement is judged on price
    when the previous hit's yield is unknown."""
    if previous is None:
        return True
    if hit.side == "ask":
        if previous.ytm is not None and hit.ytm is not None:
            return hit.ytm - previous.ytm >= REALERT_YTM_PTS
        return hit.price <= previous.price * (1 - REALERT_BID_PCT / 100)
    return hit.price >= previous.price * (1 + REALERT_BID_PCT / 100)


def _price(v: float) -> str:
    """₹997 for whole rupees, ₹1,314.80 otherwise."""
    return f"₹{v:,.0f}" if float(v).is_integer() else f"₹{v:,.2f}"


def _rating(text: str | None) -> str | None:
    """'CRISIL: BBB-/Stable' -> 'CRISIL BBB-/Stable'."""
    if not text:
        return None
    return text.replace(": ", " ", 1).strip()


def _when(at: datetime | None) -> str:
    return (at or datetime.now(IST)).astimezone(IST).strftime("%H:%M:%S IST")


def _bond_lines(hit: Hit, bond: dict[str, Any]) -> list[str]:
    """Rating + maturity, then symbol + ISIN - the same on every push."""
    rating = _rating(bond.get("credit_rating"))
    maturity = (f"matures {bond['maturity_date']:%b %Y}"
                if isinstance(bond.get("maturity_date"), date) else None)
    symbol = bond.get("nse_symbol") or bond.get("bse_scrip_id")
    lines = [", ".join(p for p in (rating, maturity) if p) or None,
             f"{symbol}, {hit.isin}" if symbol else hit.isin]
    return [line for line in lines if line]


def render(hit: Hit, bond: dict[str, Any], *, domain: str,
           tick_at: datetime | None = None) -> tuple[str, str, str]:
    """(title, body, click_url) for the push and the bell entry: the issuer
    and the yield up top, then one fact per line - the ask, the bond, the
    identifiers, the rule, the time."""
    name = (bond.get("issuer_name") or hit.isin).strip()
    units = f"{hit.qty:,.0f}"
    if hit.side == "ask":
        title = f"{name} — {hit.ytm:.2f}%" if hit.ytm is not None else f"{name} — ask {_price(hit.price)}"
    else:
        title = f"{name} — bid {_price(hit.price)}" + (f" ({hit.ytm:.2f}%)" if hit.ytm is not None else "")
    ask_line = f"{_price(hit.price)} × {units} = {_price(hit.price * hit.qty)}"
    if hit.exchange:
        ask_line += f" on {hit.exchange}"
    lines = [ask_line, *_bond_lines(hit, bond)]
    if hit.rule_label:
        lines.append(f"Rule: {hit.rule_label}")
    lines.append(_when(tick_at))
    url = f"https://{domain}/?bond={hit.isin}&tab=order%20book"
    return title, "\n".join(lines), url


# --- Auto-buy -------------------------------------------------------------------

def size_order(budget: float | None, ask_price: float, ask_qty: float) -> int:
    """Units to buy: as many as the budget affords at the ask, never more
    than are resting there. 0 when the budget buys less than one unit."""
    if budget is None or ask_price <= 0 or ask_qty <= 0:
        return 0
    return int(min(math.floor(budget / ask_price), math.floor(ask_qty)))


def auto_buy_gate(rule: AskRule, hit: Hit, *, now: datetime, spent_rule: float, spent_all: float,
                  global_cap: float | None, cash: float | None, already_ordered: bool,
                  qty: int, blacklisted: str | None | bool = False) -> str | None:
    """The reason NOT to place an order, or None when every fence passes.
    Checked in this order so the recorded reason is the first real blocker.
    `blacklisted` is False when the bond is not on the user's never-auto-buy
    list, else its note (or None for no note): that fence comes first."""
    if blacklisted is not False:
        return f"blacklisted: {blacklisted}" if blacklisted else "blacklisted"
    if not rule.armed(now):
        return "not armed"
    if rule.buy_budget is None or rule.buy_daily_cap is None:
        return "no budget or daily cap set"
    if shortfall(hit) is not None:
        return f"exact yield {hit.ytm:.2f}% is short of the rule"
    if qty < 1:
        return f"budget {_inr_short(rule.buy_budget)} buys 0 units at ₹{hit.price:,.2f}"
    if already_ordered:
        return "already ordered this bond today"
    value = qty * hit.price
    if spent_rule + value > rule.buy_daily_cap:
        return f"rule daily cap reached ({_inr_short(spent_rule)} of {_inr_short(rule.buy_daily_cap)})"
    if global_cap is not None and spent_all + value > global_cap:
        return f"global daily cap reached ({_inr_short(spent_all)} of {_inr_short(global_cap)})"
    if cash is not None and cash < value:
        return f"available cash {_inr_short(cash)} < order {_inr_short(value)}"
    return None


def render_order(status: str, qty: int, hit: Hit, bond: dict[str, Any], *,
                 reason: str | None = None, order_id: str | None = None,
                 at: datetime | None = None) -> tuple[str, str]:
    """(title, body) for the auto-buy push and bell row. 'placed' is an
    order on the book, not a fill - a taken ask leaves it resting."""
    name = (bond.get("issuer_name") or hit.isin).strip()
    value = qty * hit.price
    where = f" on {hit.exchange}" if hit.exchange else ""
    attempt = f"{qty} × {_price(hit.price)} = {_price(value)}{where}"
    if status == "placed":
        title = f"Order placed: {qty} × {name} — {_price(value)}"
        lines = [f"LIMIT at {_price(hit.price)}{where}, Kite order {order_id}"]
    elif status == "rejected":
        title = f"Auto-buy rejected — {name}"
        lines = [f"Kite: {reason}", f"Tried {attempt}"]
    else:
        title = f"Auto-buy skipped — {name}"
        lines = [reason or "skipped"] + ([f"Would have been {attempt}"] if qty > 0 else [])
    if hit.rule_label:
        lines.append(f"Rule: {hit.rule_label}")
    lines.append(_when(at))
    return title, "\n".join(lines)
