"""Yield-to-maturity calculation.

Assumptions (kept simple for a personal screener):
- Annual coupon payments (many Indian retail bonds pay monthly/annual;
  annual is a reasonable first approximation for screening).
- NSE's retail bond series trade on a dirty-price basis: the traded price
  already includes accrued interest, so LTP is used as-is when solving.
- Coupons falling within ~15 days are assumed to belong to the seller
  (record date passed) — without this, the estimate overstates YTM by
  roughly one coupon whenever a payment is imminent.
- Actual/365 day count for time to maturity.
"""
from datetime import date, datetime
from typing import Optional


def parse_date(value: str):
    """Parse NSE date strings like '15-Sep-2027'."""
    if not value:
        return None
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def years_to_maturity(maturity: date, today: Optional[date] = None) -> Optional[float]:
    today = today or date.today()
    if maturity is None or maturity <= today:
        return None
    return (maturity - today).days / 365.0


# Coupons due within this window are assumed already ex-interest: the record
# date (typically ~15 days before payout) has passed, so the seller keeps them.
_RECORD_WINDOW_YEARS = 15 / 365.0

# Solutions outside this band almost always mean defective input data
# (truncated schedules, issue-level amounts, partially redeemed face) —
# better to show nothing than a wrong number.
_MIN_PLAUSIBLE_YTM = -30.0
_MAX_PLAUSIBLE_YTM = 100.0


def _plausible(ytm_pct: float) -> Optional[float]:
    if _MIN_PLAUSIBLE_YTM <= ytm_pct <= _MAX_PLAUSIBLE_YTM:
        return ytm_pct
    return None


def _price_for_yield(rate: float, coupon_amt: float, face_value: float, years: float) -> float:
    """Present value of remaining cash flows at a given annual yield."""
    price = 0.0
    # Coupon dates approximated as fractional years counting back from maturity
    t = years
    while t > _RECORD_WINDOW_YEARS:
        price += coupon_amt / (1 + rate) ** t
        t -= 1.0
    price += face_value / (1 + rate) ** years
    return price


def accrued_interest(cashflows: list, today: Optional[date] = None) -> Optional[float]:
    """Accrued interest per unit, interpolated linearly across the current
    coupon period of the NSDL schedule. None when the period can't be located
    (e.g. still inside the first coupon period).
    """
    today = today or date.today()
    interest = []
    for f in cashflows:
        if "INTEREST" not in (f.get("event") or "").upper():
            continue
        due = parse_date(f.get("due_date"))
        if due and f.get("amount"):
            interest.append((due, f["amount"]))
    interest.sort()
    prev = max((d for d, _ in interest if d <= today), default=None)
    nxt = next(((d, a) for d, a in interest if d > today), None)
    if prev is None or nxt is None or nxt[0] <= prev:
        return None
    elapsed = (today - prev).days / (nxt[0] - prev).days
    return round(nxt[1] * elapsed, 2)


def modified_duration(price: float, cashflows: list, ytm_pct: Optional[float],
                      today: Optional[date] = None) -> Optional[float]:
    """Modified duration in years from dated cash flows.

    Approximate % price drop for a 1% rise in market yields. Macaulay duration
    (PV-weighted average time of the flows a buyer today receives) divided by
    (1 + y).
    """
    if not price or ytm_pct is None:
        return None
    y = ytm_pct / 100.0
    if y <= -1:
        return None
    today = today or date.today()
    pv = weighted = 0.0
    for f in cashflows:
        due = parse_date(f.get("due_date"))
        if due is None or due <= today:
            continue
        record = parse_date(f.get("record_date"))
        if record is not None and record <= today:
            continue
        amount = f.get("amount")
        if not amount:
            continue
        t = (due - today).days / 365.0
        d = amount / (1 + y) ** t
        pv += d
        weighted += t * d
    if pv <= 0:
        return None
    return round(weighted / pv / (1 + y), 2)


def exact_ytm(price: float, cashflows: list, today: Optional[date] = None) -> Optional[float]:
    """XIRR-style YTM (in %) from dated cash flows against the traded price.

    ``cashflows`` are dicts with due_date, record_date, amount (per unit of
    face value, as published by NSDL). A flow is received by the buyer only
    if its record date (fallback: due date) is still in the future.
    """
    today = today or date.today()
    flows = []
    for f in cashflows:
        due = parse_date(f.get("due_date"))
        if due is None or due <= today:
            continue
        record = parse_date(f.get("record_date"))
        if record is not None and record <= today:
            continue
        amount = f.get("amount")
        if not amount:
            continue
        flows.append(((due - today).days / 365.0, amount))
    if not price or not flows:
        return None

    def pv(rate: float) -> float:
        return sum(amt / (1 + rate) ** t for t, amt in flows)

    lo, hi = -0.50, 2.00
    if pv(lo) < price or pv(hi) > price:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if pv(mid) > price:
            lo = mid
        else:
            hi = mid
    return _plausible(round((lo + hi) / 2 * 100, 2))


def ytm(price: float, coupon_pct: Optional[float], face_value: float, maturity_str: str,
        today: Optional[date] = None) -> Optional[float]:
    """Solve annual YTM (in %) by bisection. Returns None if inputs are unusable."""
    maturity = parse_date(maturity_str)
    years = years_to_maturity(maturity, today)
    if not price or not face_value or years is None:
        return None
    coupon_amt = face_value * (coupon_pct or 0.0) / 100.0

    lo, hi = -0.50, 2.00  # -50% to 200%
    if _price_for_yield(lo, coupon_amt, face_value, years) < price:
        return None
    if _price_for_yield(hi, coupon_amt, face_value, years) > price:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if _price_for_yield(mid, coupon_amt, face_value, years) > price:
            lo = mid
        else:
            hi = mid
    return _plausible(round((lo + hi) / 2 * 100, 2))
