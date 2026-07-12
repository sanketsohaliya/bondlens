"""Day-count conventions for accrued interest and year fractions.

NSDL's bond_terms.day_count_convention is dominated by 'Actual/actual'
(~87%), with a long NULL tail and a handful of 30/360 and Actual/365.
Unknown/NULL falls back to Actual/Actual — the Indian corporate-bond
default per SEBI's 2013 circular.
"""
from datetime import date

ACT_ACT = "act/act"
ACT_365 = "act/365"
THIRTY_360 = "30/360"

_ALIASES = {
    "actual/actual": ACT_ACT,
    "actual / actual": ACT_ACT,
    "act/act": ACT_ACT,
    "actual/365": ACT_365,
    "act/365": ACT_365,
    "actual/365 (fixed)": ACT_365,
    "30/360": THIRTY_360,
    "30e/360": THIRTY_360,
}


def normalize(convention: str | None) -> str:
    """Map NSDL's free-text convention to a canonical constant."""
    return _ALIASES.get((convention or "").strip().lower(), ACT_ACT)


def year_frac(start: date, end: date, convention: str = ACT_ACT) -> float:
    """Year fraction between two dates under a day-count convention.

    For Act/Act the ISDA period-wise variant is overkill for screening;
    the actual-days / days-in-year(365 or 366 per leap status of the span)
    approximation is used, which matches exchange practice for coupon
    accrual to well under a basis point on sub-year periods.
    """
    if end <= start:
        return 0.0
    if convention == THIRTY_360:
        d1 = min(start.day, 30)
        d2 = min(end.day, 30) if d1 == 30 else end.day
        return ((end.year - start.year) * 360
                + (end.month - start.month) * 30
                + (d2 - d1)) / 360.0
    days = (end - start).days
    if convention == ACT_365:
        return days / 365.0
    # ACT/ACT: use 366 only if the accrual period actually spans a leap day.
    return days / (366.0 if _spans_leap_day(start, end) else 365.0)


def _spans_leap_day(start: date, end: date) -> bool:
    for year in range(start.year, end.year + 1):
        if _is_leap(year):
            leap_day = date(year, 2, 29)
            if start < leap_day <= end:
                return True
    return False


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
