"""Yield, accrued-interest and duration math over canonical Schedules.

All prices are dirty ₹ per unit (the retail trading convention) unless
stated otherwise. Yields are % annualized, compounded at the bond's actual
payment frequency (falls back to annual when the frequency is unknown).
"""
import math
import re
from dataclasses import replace
from datetime import date, datetime

from . import daycount
from .schedule import Flow, Schedule, _months_back

# Solutions outside this band almost always mean defective input data —
# better to show nothing than a wrong number.
_MIN_PLAUSIBLE = -30.0
_MAX_PLAUSIBLE = 100.0
_LO, _HI = -0.50, 2.00  # bisection band, rate per year


def _plausible(pct: float | None) -> float | None:
    if pct is None or not (_MIN_PLAUSIBLE <= pct <= _MAX_PLAUSIBLE):
        return None
    return pct


def _times_amounts(flows: list[Flow], today: date, dc: str) -> list[tuple[float, float]]:
    """Money-timing: discount to when the cash actually lands (the
    business-day-adjusted payment date when published). A coupon already
    paid early is not a future flow, even before its due date."""
    return [(daycount.year_frac(today, f.effective_date, dc), f.amount)
            for f in flows if f.amount and f.effective_date > today]


def _solve(price: float, tv: list[tuple[float, float]], m: int) -> float | None:
    """Bisection solve for the annual rate (compounded m times/yr) that
    prices the flows at ``price``. None when no root is bracketed."""
    if (not math.isfinite(price) or price <= 0 or not tv or m <= 0
            or any(not math.isfinite(t) or not math.isfinite(a) or t < 0 or a < 0 for t, a in tv)):
        return None

    def pv(rate: float) -> float:
        # (1+r/m)^(m*t) underflows to 0 for extreme negative rates on very
        # far-dated flows (placeholder maturities like 2999) — treat the
        # discounted value as infinite so bisection brackets correctly.
        total = 0.0
        for t, a in tv:
            try:
                denom = (1 + rate / m) ** (m * t)
            except OverflowError:
                continue  # denominator ~inf -> flow contributes ~0
            if denom == 0:
                return float("inf")
            total += a / denom
        return total

    lo, hi = _LO, _HI
    if pv(lo) < price or pv(hi) > price:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if pv(mid) > price:
            lo = mid
        else:
            hi = mid
    return _plausible(round((lo + hi) / 2 * 100, 4))


def exact_ytm(price: float, sched: Schedule,
              today: date | None = None) -> float | None:
    """YTM (%) from the dirty price against the buyer-receivable flows,
    compounded at the bond's payment frequency."""
    today = today or date.today()
    if (not sched.usable or sched.perpetual or sched.shape == "perpetual"
            or sched.pricing_issues(today)):
        return None
    m = sched.periods_per_year or 1
    tv = _times_amounts(sched.future_flows(today), today, sched.day_count)
    y = _solve(price, tv, m)
    return round(y, 2) if y is not None else None


def yield_to_worst(price: float, sched: Schedule, call_dates: list[date],
                   today: date | None = None) -> float | None:
    """Lowest yield across maturity and each call scenario.

    A call scenario truncates the schedule at the call date and redeems
    the outstanding face there under a par-call assumption. Unsupported
    premiums or unresolved scenario cashflows withhold yield-to-worst.
    """
    today = today or date.today()
    ytm = exact_ytm(price, sched, today)
    if ytm is None or not call_dates or not sched.face_value:
        return ytm
    worst = ytm
    for cd in sorted(set(call_dates)):
        if cd <= today:
            continue
        if sched.maturity_date is not None and cd > sched.maturity_date:
            continue
        y = yield_to_call(price, sched, [cd], today)
        if y is None:
            return None
        worst = min(worst, y)
    return worst


def yield_to_call(price: float | None, sched: Schedule,
                  call_dates: list[date],
                  today: date | None = None) -> float | None:
    """Yield to the earliest future call: coupons to the call date plus
    redemption of the outstanding face there. The maturity leg is
    deliberately NOT considered — this is for perpetuals, where a
    maturity-based YTM is meaningless."""
    today = today or date.today()
    flows = call_cashflows(sched, call_dates, today)
    if not price or flows is None:
        return None
    tv = _times_amounts(flows, today, sched.day_count)
    y = _solve(price, tv, sched.periods_per_year or 1)
    return round(y, 2) if y is not None else None


def call_cashflows(sched: Schedule, call_dates: list[date], today: date) -> list[Flow] | None:
    if not sched.face_value or not sched.flows or sched.call_price_unsupported:
        return None
    cd = next((d for d in sorted(set(call_dates)) if today < d and d.year < 2500), None)
    if cd is None:
        return None
    outstanding = _outstanding_at(sched, cd, today)
    sched = replace(sched, flows=[replace(f, optional=False) if f.optional and f.due_date == cd else f
                                 for f in sched.flows])
    if sched.pricing_issues(today, until=cd):
        return None
    if sched.maturity_date is not None and cd > sched.maturity_date:
        return None
    coupons = [f for f in sched.flows if f.event == "interest"]
    if coupons and not any(f.due_date == cd for f in coupons):
        return None
    if outstanding is None or outstanding <= 0:
        return None
    same_date_redemptions = [f for f in sched.flows if f.event == "redemption" and f.due_date == cd]
    if any(not f.receivable_by_buyer(today) for f in same_date_redemptions):
        return None
    flows = [f for f in sched.future_flows(today) if f.due_date < cd
             or (f.due_date == cd and f.event == "interest")]
    payment_date = None
    if math.isclose(sum(f.amount or 0 for f in same_date_redemptions), outstanding, rel_tol=1e-6, abs_tol=0.01):
        payment_dates = {f.payment_date for f in same_date_redemptions}
        if len(payment_dates) != 1:
            return None
        payment_date = next(iter(payment_dates))
    flows.append(Flow(event="redemption", due_date=cd, amount=outstanding, payment_date=payment_date,
                      adjustments=[{"kind": "call_scenario", "previous_amount": None, "amount": outstanding,
                                    "reason": "Conditional call scenario: outstanding principal redeemed at par; verify exercise and contractual call price"}]))
    return flows


def implied_coupon(sched: Schedule,
                   issue_price: float | None = None) -> float | None:
    """Implied effective-annual rate (%) for bonds that pay everything at
    the end (shapes ``cumulative`` and ``zero_coupon``), where NSDL's
    couponRate is prose ("N.A.", "effective yield ...") and parses to None.

    Solves (total_payout / base)^(1/T) = 1 + r over T years from allotment
    to the final flow. ``base`` is the face value; when the redemption is
    at par (a discount-issued zero coupon) the return sits in the issue
    price instead, so that is used when supplied.

    Deliberately effective-annual: it is the only quoting-convention-free
    measure (issuers advertise the same cashflows as simple, monthly- or
    annually-compounded rates), and it matches the compounding our YTM
    uses. Display as ≈implied, never as the official coupon.
    """
    if sched.shape not in ("cumulative", "zero_coupon") or not sched.usable:
        return None
    if sched.allotment_date is None or not sched.face_value or sched.day_count not in daycount.SUPPORTED:
        return None
    dated = [(f.due_date, f.amount) for f in sched.flows
             if f.amount and f.due_date]
    if not dated:
        return None
    total = sum(a for _, a in dated)
    last = max(d for d, _ in dated)
    base = float(sched.face_value)
    if total <= base * 1.001:
        # Redeems at par: the gain is the issue discount, if we know it.
        if not issue_price or not 0 < float(issue_price) < total:
            return None
        base = float(issue_price)
    t = daycount.year_frac(sched.allotment_date, last, sched.day_count)
    if t < 0.25:
        return None
    return _plausible(round(((total / base) ** (1 / t) - 1) * 100, 2))


def parse_call_dates(terms: dict | None) -> list[date]:
    """Extract usable call dates from bond_terms' free-text fields."""
    if not terms or (terms.get("call_indicator") or "").strip().lower() != "yes":
        return []
    text = str(terms.get("call_specified_dates") or "")
    dates = []
    for match in re.finditer(r"\b\d{4}-\d{2}-\d{2}\b", text):
        try:
            dates.append(date.fromisoformat(match.group()))
        except ValueError:
            continue
    for m in re.finditer(r"\b(\d{1,2})[-/](\d{1,2}|[A-Za-z]{3,9})[-/](\d{4})\b", text):
        d, mth, y = m.groups()
        try:
            if mth.isdigit():
                dates.append(date(int(y), int(mth), int(d)))
            else:
                dates.append(datetime.strptime(
                    f"{d}-{mth[:3]}-{y}", "%d-%b-%Y").date())
        except ValueError:
            continue
    # Prose style dominates the feed: 'NOVEMBER 24, 2026' / 'DECEMBER 17 2024'.
    for m in re.finditer(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", text):
        mon, d, y = m.groups()
        try:
            dates.append(datetime.strptime(
                f"{d}-{mon[:3]}-{y}", "%d-%b-%Y").date())
        except ValueError:
            continue
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9}),?\s+(\d{4})\b", text, re.I):
        d, mon, y = m.groups()
        try:
            dates.append(datetime.strptime(f"{d}-{mon[:3]}-{y}", "%d-%b-%Y").date())
        except ValueError:
            continue
    return sorted(d for d in set(dates) if d.year < 2500)


def _outstanding_at(sched: Schedule, on: date, today: date | None = None) -> float | None:
    """Face outstanding before the call, reconciling original/current face."""
    return sched.principal_at(on, today or date.today())


def accrued_interest(sched: Schedule,
                     today: date | None = None) -> float | None:
    """Accrued interest per unit under the bond's day-count convention.

    Accrues the next coupon across [previous coupon date, next coupon
    date). In the first coupon period (no previous coupon yet) interest
    accrues from the allotment date. None when the position inside the
    coupon stream can't be located at all.

    Ex-interest window: once the next coupon's record date has passed, a
    buyer settling today no longer receives it, so accrued turns negative
    (the seller keeps the full coupon; the buyer is compensated for the
    days between settlement and the payment date).
    """
    today = today or date.today()
    if sched.day_count not in daycount.SUPPORTED:
        return None
    interest = sorted((f for f in sched.flows
                       if f.event == "interest" and f.amount),
                      key=lambda f: f.due_date)
    prev = None
    nxt = None
    for f in interest:
        if f.due_date <= today:
            prev = f
        elif nxt is None:
            nxt = f
    if nxt is None or nxt.amount is None:
        return None
    prev_date = prev.due_date if prev is not None else sched.allotment_date
    if prev_date is None or prev_date > today:
        return None
    total = daycount.year_frac(prev_date, nxt.due_date, sched.day_count)
    if total <= 0:
        return None
    if not nxt.receivable_by_buyer(today):
        # Ex-interest: buyer misses the whole coupon, so accrued is minus
        # the unearned remainder of the period (settlement -> due date).
        remaining = daycount.year_frac(today, nxt.due_date, sched.day_count)
        return round(-nxt.amount * max(0.0, min(1.0, remaining / total)), 2)
    elapsed = daycount.year_frac(prev_date, today, sched.day_count)
    return round(nxt.amount * min(1.0, elapsed / total), 2)


def clean_price(dirty: float | None, sched: Schedule,
                today: date | None = None) -> float | None:
    acc = accrued_interest(sched, today)
    return round(dirty - acc, 2) if acc is not None and dirty else None


def dirty_from_clean_pct(clean_pct: float, sched: Schedule,
                         today: date | None = None) -> float | None:
    """OTC quotes are clean % of face; convert to dirty ₹ per unit for
    comparison with retail prices."""
    if not clean_pct or not sched.face_value:
        return None
    acc = accrued_interest(sched, today)
    if acc is None:
        return None
    return round(clean_pct / 100.0 * sched.face_value + acc, 2)


def modified_duration(price: float | None, sched: Schedule,
                      ytm_pct: float | None,
                      today: date | None = None) -> float | None:
    """Modified duration (yrs): % price drop per 1% yield rise."""
    if not price or ytm_pct is None or not sched.usable:
        return None
    today = today or date.today()
    if sched.perpetual or sched.shape == "perpetual" or sched.pricing_issues(today):
        return None
    m = sched.periods_per_year or 1
    y = ytm_pct / 100.0
    if y / m <= -1:
        return None
    pv = weighted = 0.0
    for t, a in _times_amounts(sched.future_flows(today), today, sched.day_count):
        d = a / (1 + y / m) ** (m * t)
        pv += d
        weighted += t * d
    if pv <= 0:
        return None
    return round(weighted / pv / (1 + y / m), 2)


def approx_ytm(price: float, coupon_pct: float | None, face_value: float | None,
               maturity: date | None, periods_per_year: int = 1,
               today: date | None = None) -> float | None:
    """Fallback YTM from headline terms when no usable schedule exists:
    synthesizes level coupons from today to maturity."""
    today = today or date.today()
    if (not price or not face_value or coupon_pct is None
            or maturity is None or maturity <= today or maturity.year >= 2500):
        return None
    m = periods_per_year or 1
    if m not in (1, 2, 4, 12):
        return None
    years = (maturity - today).days / 365.0
    per_coupon = face_value * coupon_pct / 100.0 / m
    tv = []
    index = 0
    # A near-term coupon needs actual accrual/entitlement information;
    # withhold the approximation rather than silently omitting that coupon.
    while coupon_pct and (due := _months_back(maturity, index * (12 // m))) > today:
        t = (due - today).days / 365.0
        if t <= 0.5 / m:
            return None
        tv.append((t, per_coupon))
        index += 1
    if not tv:
        tv = [(years, 0.0)]
    tv[0] = (years, tv[0][1] + face_value)  # redemption rides the last flow
    y = _solve(price, tv, m)
    return round(y, 2) if y is not None else None


def fallback_ytm(price: float, sched: Schedule, terms: dict | None,
                 today: date) -> float | None:
    terms = terms or {}
    if (sched.pricing_issues(today) or sched.perpetual or sched.shape == "perpetual"
            or sched.flows or sched.call_price_unsupported
            or (sched.coupon_rate and sched.periods_per_year not in (1, 2, 4, 12))
            or str(terms.get("coupon_basis") or "").lower().startswith("variable")
            or any(word in str(terms.get("coupon_type") or "").lower() for word in ("float", "compound"))):
        return None
    return approx_ytm(price, sched.coupon_rate, sched.face_value, sched.maturity_date,
                      sched.periods_per_year or 1, today)


def call_terms_unresolved(terms: dict | None) -> bool:
    if not terms or str(terms.get("call_indicator") or "").strip().lower() != "yes":
        return False
    text = str(terms.get("call_specified_dates") or "")
    return not parse_call_dates(terms) or bool(re.search(
        r"thereafter|therafter|anniversary|any\s*time|on\s+or\s+after", text, re.I))


def effective_annual(nominal_pct: float | None, periods_per_year: int | None) -> float | None:
    if (nominal_pct is None or not math.isfinite(nominal_pct)
            or periods_per_year not in (1, 2, 4, 12)):
        return None
    assert periods_per_year is not None
    periodic = nominal_pct / 100 / periods_per_year
    if periodic <= -1:
        return None
    try:
        result = math.expm1(periods_per_year * math.log1p(periodic)) * 100
    except OverflowError:
        return None
    return result if math.isfinite(result) else None


def current_yield(price: float | None, coupon_pct: float | None,
                  face_value: float | None) -> float | None:
    if not price or not coupon_pct or not face_value:
        return None
    return round(coupon_pct * face_value / price, 2)


def xirr(dated_flows: list[tuple[date, float]]) -> float | None:
    """Annualized IRR (%) over signed dated flows (outflows negative)."""
    flows = [(d, a) for d, a in dated_flows if d is not None and a]
    if len(flows) < 2:
        return None
    if not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return None
    t0 = min(d for d, _ in flows)

    def npv(rate: float) -> float:
        return sum(a / (1 + rate) ** ((d - t0).days / 365.0) for d, a in flows)

    lo, hi = _LO, _HI
    v_lo, v_hi = npv(lo), npv(hi)
    if v_lo * v_hi > 0:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if (npv(mid) > 0) == (v_lo > 0):
            lo = mid
        else:
            hi = mid
    return _plausible(round((lo + hi) / 2 * 100, 2))
