import hashlib
import json
import math
import re
from dataclasses import asdict
from datetime import UTC, date, datetime

from . import yields
from .schedule import Flow, Schedule, maturity_mismatch

VERSION = "2026-10-28.1"
RELIABLE = (-1.0, 50.0)
RELIABLE_NEAR = (0.0, 20.0)
NEAR_DAYS = 90
OTC_MAX_GAP = 3.0
OTC_MAX_AGE = 14


def warning(code: str, message: str, severity: str = "caution", **fields) -> dict:
    return {"code": code, "message": message, "severity": severity, **fields}


def cautions(value: float | None, horizon: date | None, today: date,
             coupon_basis: str | None = None, otc_yield: float | None = None,
             otc_trade_date: date | None = None, coupon_type: str | None = None,
             otc_price: float | None = None, price: float | None = None) -> list[dict]:
    result = []
    if otc_trade_date and otc_trade_date > today:
        result.append(warning("future_otc_date", "OTC print is future-dated and cannot be used as a current comparison"))
    if value is None:
        return result
    if re.search(r"variable|float|market.?linked|index.?linked|\b(reset|mibor|repo)\b",
                 f"{coupon_basis or ''} {coupon_type or ''}", re.I):
        result.append(warning("variable_coupon", "Variable or linked coupon: published future payments may assume today's rate; this is conditional, not a fixed return"))
    if not RELIABLE[0] <= value <= RELIABLE[1]:
        result.append(warning("yield_range", f"Yield {value:.2f}% is outside the sanity range {RELIABLE[0]:g}% to {RELIABLE[1]:g}%; this is a review flag, not proof of an error"))
    if horizon is not None and 0 <= (days := (horizon - today).days) < NEAR_DAYS:
        result.append(warning("short_horizon", f"Only {days} calendar days to the scenario's final payment; annualized yield is sensitive to price, timing and charges", "info"))
        if not RELIABLE_NEAR[0] <= value <= RELIABLE_NEAR[1]:
            result.append(warning("short_horizon_range", f"Short-horizon yield {value:.2f}% is outside {RELIABLE_NEAR[0]:g}% to {RELIABLE_NEAR[1]:g}%; inspect the rupee gain before fees and taxes"))
    if (otc_yield is not None and math.isfinite(otc_yield) and otc_trade_date
          and 0 <= (today - otc_trade_date).days <= OTC_MAX_AGE
          and abs(value - otc_yield) > OTC_MAX_GAP):
        # Signed: positive means the retail yield is above the OTC print (so
        # the retail price is below institutional). The UI composes its own
        # direction-specific wording from these fields; `message` is the
        # plain-text form quoted into the AI prompt.
        gap = round(value - otc_yield, 2)
        direction = "above" if gap > 0 else "below"
        # When the OTC print can be expressed in rupees (dirty, per unit) the
        # comparison is stated as a price gap in the same units as the LTP
        # the reader is looking at; the yield gap is the fallback.
        extra: dict = {}
        if otc_price is not None and price is not None and otc_price > 0:
            price_gap = round(price - otc_price, 2)          # + means retail dearer
            price_gap_pct = round(price_gap / otc_price * 100, 2)
            extra = {"otc_price": round(otc_price, 2), "retail_price": round(price, 2),
                     "price_gap": price_gap, "price_gap_pct": price_gap_pct}
            cheaper = "cheaper" if price_gap < 0 else "more"
            message = (f"Institutions paid about Rs {otc_price:,.0f} per unit on {otc_trade_date} (OTC); "
                       f"retail last traded at Rs {price:,.0f} - Rs {abs(price_gap):,.0f} "
                       f"({abs(price_gap_pct):.1f}%) {cheaper}. Yields {value:.2f}% retail vs {otc_yield:.2f}% OTC.")
        else:
            message = (f"Retail yield {value:.2f}% is {abs(gap):.2f} pts {direction} the recent OTC yield "
                       f"{otc_yield:.2f}% ({otc_trade_date}). OTC and retail quotes can differ in price, "
                       f"date and convention; treat as market context, not a calculation error.")
        result.append(warning("otc_difference", message, "info",
                              otc_yield=otc_yield, otc_trade_date=otc_trade_date.isoformat(), gap=gap, **extra))
    return result


def scenario_flows(sched: Schedule, terms: dict | None, today: date) -> list[Flow] | None:
    if sched.perpetual or sched.shape == "perpetual":
        return yields.call_cashflows(sched, yields.parse_call_dates(terms), today)
    return [f for f in sched.flows if f.receivable_by_buyer(today)]


def flow_data(flow: Flow, today: date) -> dict:
    return {"event": flow.event, "due_date": flow.due_date, "record_date": flow.record_date,
            "payment_date": flow.payment_date, "amount": flow.amount, "estimated": flow.estimated,
            "future": flow.receivable_by_buyer(today), "optional": flow.optional, "adjustments": flow.adjustments}


def calculate(price: float | None, sched: Schedule, terms: dict | None, today: date, *,
              allow_approx: bool = False, otc_yield: float | None = None,
              otc_trade_date: date | None = None, price_date: date | None = None,
              otc_price_pct: float | None = None) -> dict:
    terms = terms or {}
    perpetual = sched.perpetual or sched.shape == "perpetual"
    calls = yields.parse_call_dates(terms)
    target = next((d for d in calls if d > today), None) if perpetual else sched.maturity_date
    flows = scenario_flows(sched, terms, today)
    issues = sched.pricing_issues(today, until=target if perpetual else None)
    estimated = sched.yield_amounts_estimated(today, until=target if perpetual else None)
    valid_price = price is not None and math.isfinite(price) and price > 0
    value = method = None
    if valid_price:
        assert price is not None
        value = yields.yield_to_call(price, sched, calls, today) if perpetual else yields.exact_ytm(price, sched, today)
        if value is not None:
            method = "ytc" if perpetual else "estimated" if estimated else "exact"
        elif not perpetual and allow_approx:
            value = yields.fallback_ytm(price, sched, terms, today)
            if value is not None:
                method = "approx"
    horizon = max((f.effective_date for f in flows or []), default=target)
    otc_price = (yields.dirty_from_clean_pct(otc_price_pct, sched, today)
                 if otc_price_pct else None)
    warnings = cautions(value, horizon, today, terms.get("coupon_basis"), otc_yield,
                        otc_trade_date, terms.get("coupon_type"),
                        otc_price=otc_price, price=price)
    warnings.extend(warning("schedule_invalid", issue, "error") for issue in issues)
    mismatch = maturity_mismatch(sched, terms)
    if mismatch:
        last, stated = mismatch
        warnings.append(warning(
            "maturity_mismatch",
            f"Cashflow schedule redeems on {last:%d %b %Y} but the terms state maturity "
            f"{stated:%d %b %Y}; the yield is computed on the schedule and may be wrong",
            "error", schedule_date=last.isoformat(), stated_date=stated.isoformat()))
    if not valid_price:
        warnings.append(warning("price_unavailable", "No finite positive price is available for this calculation", "error"))
    elif value is None:
        warnings.append(warning("yield_unavailable", "No supported yield is available at this price and scenario; this is not a zero return", "error"))
    if estimated:
        warnings.append(warning("estimated_payments", "Amounts used in this yield are estimated; inspect the per-payment adjustments"))
    inferred = [f for f in sched.flows if any(a["kind"] == "coupon_inferred" for a in f.adjustments)]
    if inferred:
        dates = [f.due_date for f in inferred]
        amounts = [f.amount or 0 for f in inferred]
        n = len(inferred)
        rate = f"{terms.get('coupon_rate'):g}%" if terms.get("coupon_rate") else "the published rate"
        cadence = {12: " monthly", 4: " quarterly", 2: " semi-annual", 1: " annual"}.get(sched.periods_per_year or 0, "")
        when = ", ".join(d.strftime("%d %b %Y") for d in dates[:-1]) + (" and " if n > 1 else "") + dates[-1].strftime("%d %b %Y")
        rupees = ", ".join(f"₹{a:,.0f}" for a in amounts)
        warnings.append(warning(
            "coupon_inferred",
            f"Coupon{'s' if n != 1 else ''} on {n} principal-repayment date{'s' if n != 1 else ''} added by BondLens. "
            f"NSDL lists only the repayment on {when}; the {rate}{cadence} coupon for "
            f"{'those months' if n != 1 else 'that month'} ({rupees}) is included because the bond keeps "
            "paying interest until each piece of principal is returned.",
            "info", count=n, rate=rate, dates=[d.isoformat() for d in dates], amounts=amounts))
    if method == "approx":
        warnings.append(warning("approximation", "Approximation from terms, not a complete published cashflow schedule"))
    if perpetual:
        warnings.append(warning("call_scenario", f"Conditional yield to call{f' on {target}' if target else ''}, assuming exercise and repayment of outstanding principal at par; not maturity YTM or guaranteed repayment"))
    elif any(d > today for d in calls):
        warnings.append(warning("callable_maturity", "Maturity YTM assumes no early call; an available call can shorten the investment and produce a different return"))
    if yields.call_terms_unresolved(terms):
        warnings.append(warning("unresolved_calls", "Some call terms are unresolved or recurring; a complete yield-to-worst is unavailable"))
    if price_date and price_date > today:
        warnings.append(warning("future_price_date", "Price is future-dated; verify the source before using this valuation"))
    elif price_date and (today - price_date).days > 7:
        # Information about the price's age, not doubt about the arithmetic:
        # a week without a trade is ordinary for Indian corporate bonds, and
        # the explorer already shows "N days ago" beside the row. As a
        # caution this marked ~60% of bonds unreliable; as info it feeds the
        # tooltip only.
        warnings.append(warning("old_price", f"Price observation is {(today - price_date).days} days old; it is not a current executable offer", "info"))
    complete = (bool(flows) and not issues and (perpetual or sched.usable) and method != "approx"
                and not mismatch)
    total = sum(f.amount or 0 for f in flows or []) if complete else None
    if total is not None and not math.isfinite(total):
        total, complete = None, False
        warnings.append(warning("invalid_payout", "Modeled payout is not finite; totals are withheld", "error"))
    gain = total - price if total is not None and valid_price and price is not None else None
    holding_return = (total / price - 1) * 100 if total is not None and valid_price and price is not None else None
    if holding_return is not None and not math.isfinite(holding_return):
        holding_return = None
    fingerprint = hashlib.sha256(json.dumps({"terms": terms, "flows": [asdict(f) for f in sched.flows]},
                                            default=str, sort_keys=True).encode()).hexdigest()[:20]
    return {"price": price if valid_price else None, "ytm": value, "ytm_method": method,
            "ytm_reliable": (not any(w["severity"] != "info" for w in warnings) if value is not None else None),
            "yield_type": "ytc" if perpetual else "ytm", "as_of": today.isoformat(),
            "calculation_version": VERSION, "calculated_at": datetime.now(UTC).isoformat(), "schedule_version": fingerprint,
            "target_date": horizon.isoformat() if horizon else None, "estimated": estimated,
            "day_count": sched.day_count, "periods_per_year": sched.periods_per_year or 1,
            "price_basis": "Modeled as dirty INR per unit; verify the quotation basis. No transaction fees or taxes included",
            "settlement_assumption": "Valuation-date discounting with record-date eligibility checks; settlement cycles, holidays and ex-date rules are not explicitly modeled",
            "cashflows_complete": complete, "cashflow_count": len(flows or []),
            "gross_payout": total, "gross_gain": gain, "holding_period_return": holding_return,
            "warnings": warnings}
