"""Yield, accrued-interest and duration math over canonical Schedules.

All prices are dirty ₹ per unit (the retail trading convention) unless
stated otherwise. Yields are % annualized, compounded at the bond's actual
payment frequency (falls back to annual when the frequency is unknown).
"""
from datetime import date

from . import daycount
from .schedule import Flow, Schedule

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
    return [(daycount.year_frac(today, f.due_date, dc), f.amount)
            for f in flows if f.amount and f.due_date > today]


def _solve(price: float, tv: list[tuple[float, float]], m: int) -> float | None:
    """Bisection solve for the annual rate (compounded m times/yr) that
    prices the flows at ``price``. None when no root is bracketed."""
    if not price or not tv:
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
    if not sched.usable:
        return None
    m = sched.periods_per_year or 1
    tv = _times_amounts(sched.future_flows(today), today, sched.day_count)
    y = _solve(price, tv, m)
    return round(y, 2) if y is not None else None


def yield_to_worst(price: float, sched: Schedule, call_dates: list[date],
                   today: date | None = None) -> float | None:
    """Lowest yield across maturity and each call scenario.

    A call scenario truncates the schedule at the call date and redeems
    the outstanding face there (standard market convention; call premia
    are rare in NSDL data and ignored).
    """
    today = today or date.today()
    ytm = exact_ytm(price, sched, today)
    if ytm is None or not call_dates or not sched.face_value:
        return ytm
    m = sched.periods_per_year or 1
    worst = ytm
    for cd in call_dates:
        if cd <= today:
            continue
        flows = [f for f in sched.future_flows(today) if f.due_date <= cd
                 and f.event == "interest"]
        flows.append(Flow(event="redemption", due_date=cd,
                          amount=_outstanding_at(sched, cd)))
        tv = _times_amounts(flows, today, sched.day_count)
        y = _solve(price, tv, m)
        if y is not None:
            worst = min(worst, round(y, 2))
    return worst


def _outstanding_at(sched: Schedule, on: date) -> float:
    """Face value still outstanding just before ``on`` (staggered bonds)."""
    face = sched.face_value or 0.0
    redeemed = sum(f.amount or 0.0 for f in sched.flows
                   if f.event == "redemption" and f.due_date < on)
    return max(0.0, face - redeemed)


def accrued_interest(sched: Schedule,
                     today: date | None = None) -> float | None:
    """Accrued interest per unit under the bond's day-count convention.

    Accrues the next coupon across [previous coupon date, next coupon
    date). None when the position inside the coupon stream can't be
    located (e.g. still in the first period with no anchor date).
    """
    today = today or date.today()
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
    if prev is None:
        return None
    total = daycount.year_frac(prev.due_date, nxt.due_date, sched.day_count)
    if total <= 0:
        return None
    elapsed = daycount.year_frac(prev.due_date, today, sched.day_count)
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
    if not price or not face_value or maturity is None or maturity <= today:
        return None
    m = periods_per_year or 1
    years = (maturity - today).days / 365.0
    per_coupon = face_value * (coupon_pct or 0.0) / 100.0 / m
    tv = []
    t = years
    # Stop half a period early: a fractional remainder would otherwise
    # synthesize a coupon paying out almost immediately.
    while t > 0.5 / m:
        tv.append((t, per_coupon))
        t -= 1.0 / m
    if not tv:
        tv = [(years, 0.0)]
    tv[0] = (years, tv[0][1] + face_value)  # redemption rides the last flow
    y = _solve(price, tv, m)
    return round(y, 2) if y is not None else None


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
