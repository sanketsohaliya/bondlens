"""Day-count conventions for accrued interest and year fractions.

NSDL's bond_terms.day_count_convention is dominated by 'Actual/actual'
(~87%), with a long NULL tail and a handful of 30/360 and Actual/365.
Missing values use the model's calendar-year Actual/Actual ISDA default;
explicit unsupported conventions remain unsupported.
"""
from datetime import date

ACT_ACT = "act/act"
ACT_365 = "act/365"
THIRTY_360 = "30/360"
THIRTY_E_360 = "30e/360"
UNSUPPORTED = "unsupported"
SUPPORTED = {ACT_ACT, ACT_365, THIRTY_360, THIRTY_E_360}

_ALIASES = {
    "actual/actual": ACT_ACT,
    "actual / actual": ACT_ACT,
    "act/act": ACT_ACT,
    "actual/actual (isda)": ACT_ACT,
    "act/act (isda)": ACT_ACT,
    "actual/365": ACT_365,
    "act/365": ACT_365,
    "actual/365 (fixed)": ACT_365,
    "30/360": THIRTY_360,
    "30e/360": THIRTY_E_360,
}


def normalize(convention: str | None) -> str:
    """Map NSDL's free-text convention to a canonical constant."""
    value = (convention or "").strip().lower()
    return _ALIASES.get(value, UNSUPPORTED) if value else ACT_ACT


def year_frac(start: date, end: date, convention: str = ACT_ACT) -> float:
    """Year fraction between two dates under a day-count convention.

    Act/Act uses calendar-year-split ISDA fractions. Generic or missing
    Act/Act is a modeling convention, not confirmation of an issue's
    quotation basis. Explicit unsupported variants must not fall back.
    """
    if convention not in SUPPORTED:
        raise ValueError(f"Unsupported day-count convention: {convention}")
    if end <= start:
        return 0.0
    if convention in (THIRTY_360, THIRTY_E_360):
        d1 = min(start.day, 30)
        d2 = min(end.day, 30) if d1 == 30 or convention == THIRTY_E_360 else end.day
        return ((end.year - start.year) * 360
                + (end.month - start.month) * 30
                + (d2 - d1)) / 360.0
    days = (end - start).days
    if convention == ACT_365:
        return days / 365.0
    # ACT/ACT: weight each calendar year's days by that year's length.
    start_basis = 366.0 if _is_leap(start.year) else 365.0
    if start.year == end.year:
        return days / start_basis
    end_basis = 366.0 if _is_leap(end.year) else 365.0
    return ((date(start.year + 1, 1, 1) - start).days / start_basis
            + end.year - start.year - 1
            + (end - date(end.year, 1, 1)).days / end_basis)


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
