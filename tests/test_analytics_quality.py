"""Regression tests for the screener data-quality fixes:
secured mapping, perpetual YTC, YTM reliability flag, rating dedupe."""
from datetime import date

from backend.analytics import ratings, yields
from backend.analytics.compute import _yn, ytm_reliability
from backend.analytics.schedule import Flow, Schedule

TODAY = date(2026, 7, 12)


# --- secured / tax_free mapping -------------------------------------------

def test_yn_recognizes_nsdl_secured_values():
    assert _yn("Secured") is True
    assert _yn("Unsecured") is False
    assert _yn("SECURED") is True
    assert _yn("unsecured") is False


def test_yn_still_handles_yes_no():
    assert _yn("Yes") is True
    assert _yn("No") is False
    assert _yn(None) is None
    assert _yn("Maybe") is None


# --- rating composite dedupe ------------------------------------------------

def _cur(*pairs):
    return [{"rating": r, "outlook": o} for r, o in pairs]


def test_composite_collapses_identical_agency_ratings():
    cur = _cur(("AAA", "Stable"), ("AAA", "Stable"), ("AAA", "Stable"))
    assert ratings.composite(cur) == "AAA/Stable ×3"


def test_composite_keeps_disagreements_visible():
    cur = _cur(("AA+", "Stable"), ("AA", "Negative"), ("AA+", "Stable"))
    assert ratings.composite(cur) == "AA+/Stable ×2, AA/Negative"


def test_composite_no_outlook_and_single():
    assert ratings.composite(_cur(("AAA", None))) == "AAA"
    assert ratings.composite(_cur(("AA", "No Outlook"))) == "AA"
    assert ratings.composite([]) is None


# --- YTM reliability ---------------------------------------------------------

def test_reliability_normal_band():
    far = date(2030, 1, 1)
    assert ytm_reliability(8.5, far, TODAY) is True
    assert ytm_reliability(-0.5, far, TODAY) is True
    assert ytm_reliability(65.0, far, TODAY) is False
    assert ytm_reliability(-27.8, far, TODAY) is False


def test_reliability_tightens_near_maturity():
    near = date(2026, 9, 1)  # < 90 days out
    assert ytm_reliability(15.0, near, TODAY) is True
    assert ytm_reliability(35.0, near, TODAY) is False   # ok far, not near
    assert ytm_reliability(-0.5, near, TODAY) is False   # ok far, not near


def test_reliability_none_cases():
    assert ytm_reliability(None, date(2030, 1, 1), TODAY) is None
    assert ytm_reliability(8.0, None, TODAY) is True  # perpetual YTC w/ bounds


# --- perpetual YTC -----------------------------------------------------------

def _perp_schedule() -> Schedule:
    flows = [Flow(event="interest", due_date=date(2026 + i, 12, 1),
                  amount=9.0) for i in range(10)]
    return Schedule(isin="TESTPERP", shape="perpetual", flows=flows,
                    face_value=100.0, frequency="annual", periods_per_year=1)


def test_yield_to_call_prices_to_the_call():
    sched = _perp_schedule()
    ytc = yields.yield_to_call(100.0, sched, [date(2028, 12, 1)], today=TODAY)
    assert ytc is not None
    # Dirty price 100 mid-coupon (buyer keeps the full near coupon), so the
    # yield sits somewhat above the 9% coupon.
    assert 9.0 < ytc < 13.0


def test_yield_to_call_none_without_dates_or_price():
    sched = _perp_schedule()
    assert yields.yield_to_call(100.0, sched, [], today=TODAY) is None
    assert yields.yield_to_call(None, sched, [date(2028, 12, 1)],
                                today=TODAY) is None


def test_parse_call_dates_moved_helper():
    terms = {"call_indicator": "Yes",
             "call_specified_dates": "call on 15-09-2027 and 15-Sep-2029"}
    assert yields.parse_call_dates(terms) == [date(2027, 9, 15),
                                              date(2029, 9, 15)]
    assert yields.parse_call_dates({"call_indicator": "No"}) == []
    assert yields.parse_call_dates(None) == []
