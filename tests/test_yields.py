"""Yield/accrued/duration math tests with hand-computed cases."""
from datetime import date

import pytest

from bondlens.analytics import daycount, yields
from bondlens.analytics.schedule import Flow, Schedule

TODAY = date(2026, 7, 12)


def _sched(flows, face=1000, ppy=1, dc=daycount.ACT_ACT, shape="plain"):
    return Schedule(isin="X", shape=shape, flows=flows, face_value=face,
                    frequency={1: "annual", 2: "semiannual", 12: "monthly"}.get(ppy),
                    periods_per_year=ppy, day_count=dc,
                    redemption_matches_face=True)


# --- day count ---------------------------------------------------------------

def test_year_frac_act_365():
    assert daycount.year_frac(date(2026, 1, 1), date(2026, 12, 31),
                              daycount.ACT_365) == pytest.approx(364 / 365)


def test_year_frac_30_360():
    assert daycount.year_frac(date(2026, 1, 15), date(2026, 7, 15),
                              daycount.THIRTY_360) == pytest.approx(0.5)


def test_year_frac_act_act_leap():
    # 2028 is a leap year; span crosses Feb 29.
    assert daycount.year_frac(date(2028, 1, 1), date(2029, 1, 1),
                              daycount.ACT_ACT) == pytest.approx(1.0)


def test_normalize():
    assert daycount.normalize("Actual/actual") == daycount.ACT_ACT
    assert daycount.normalize("30/360") == daycount.THIRTY_360
    assert daycount.normalize(None) == daycount.ACT_ACT


# --- exact YTM ---------------------------------------------------------------

def test_ytm_at_par_equals_coupon():
    flows = [Flow("interest", date(2027, 7, 12), amount=100),
             Flow("interest", date(2028, 7, 12), amount=100),
             Flow("redemption", date(2028, 7, 12), amount=1000)]
    y = yields.exact_ytm(1000 + 0.0, _sched(flows), TODAY)
    # Priced at par (dirty=clean at coupon date anniversary) -> y ~ coupon
    assert y == pytest.approx(10.0, abs=0.15)


def test_ytm_below_par_above_coupon():
    flows = [Flow("interest", date(2027, 7, 12), amount=100),
             Flow("redemption", date(2027, 7, 12), amount=1000)]
    y = yields.exact_ytm(950, _sched(flows), TODAY)
    assert y is not None and y > 10.0


def test_ytm_zero_coupon():
    # 1000 in exactly 2 years for 826.45 -> 10%
    flows = [Flow("redemption", date(2028, 7, 12), amount=1000)]
    y = yields.exact_ytm(826.45, _sched(flows, shape="zero_coupon"), TODAY)
    assert y == pytest.approx(10.0, abs=0.1)


def test_ytm_unusable_schedule_none():
    s = _sched([Flow("redemption", date(2028, 1, 1), amount=1000)])
    s.redemption_matches_face = False
    assert yields.exact_ytm(900, s, TODAY) is None


def test_ytm_implausible_price_none():
    flows = [Flow("redemption", date(2027, 7, 12), amount=1000)]
    assert yields.exact_ytm(5, _sched(flows), TODAY) is None


# --- accrued / clean ---------------------------------------------------------

def test_accrued_midway():
    flows = [Flow("interest", date(2026, 1, 12), amount=100),
             Flow("interest", date(2027, 1, 12), amount=100),
             Flow("redemption", date(2027, 1, 12), amount=1000)]
    acc = yields.accrued_interest(_sched(flows), TODAY)
    # 181/365 of the way through the coupon period
    assert acc == pytest.approx(100 * 181 / 365, abs=1.0)


def test_clean_price():
    flows = [Flow("interest", date(2026, 1, 12), amount=100),
             Flow("interest", date(2027, 1, 12), amount=100),
             Flow("redemption", date(2027, 1, 12), amount=1000)]
    s = _sched(flows)
    acc = yields.accrued_interest(s, TODAY)
    assert acc is not None
    assert yields.clean_price(1050, s, TODAY) == pytest.approx(1050 - acc)


def test_dirty_from_clean_pct_roundtrip():
    flows = [Flow("interest", date(2026, 1, 12), amount=100),
             Flow("interest", date(2027, 1, 12), amount=100),
             Flow("redemption", date(2027, 1, 12), amount=1000)]
    s = _sched(flows)
    dirty = yields.dirty_from_clean_pct(99.5, s, TODAY)
    assert yields.clean_price(dirty, s, TODAY) == pytest.approx(995.0, abs=0.01)


# --- duration ----------------------------------------------------------------

def test_duration_zero_coupon_equals_maturity_over_1py():
    flows = [Flow("redemption", date(2028, 7, 12), amount=1000)]
    s = _sched(flows, shape="zero_coupon")
    d = yields.modified_duration(826.45, s, 10.0, TODAY)
    assert d == pytest.approx(2.0 / 1.10, abs=0.05)


# --- yield to worst ----------------------------------------------------------

def test_ytw_call_below_ytm_for_premium_bond():
    flows = [Flow("interest", date(2027, 7, 12), amount=120),
             Flow("interest", date(2028, 7, 12), amount=120),
             Flow("interest", date(2029, 7, 12), amount=120),
             Flow("redemption", date(2029, 7, 12), amount=1000)]
    s = _sched(flows)
    price = 1100.0  # premium bond: early call hurts
    ytm = yields.exact_ytm(price, s, TODAY)
    ytw = yields.yield_to_worst(price, s, [date(2027, 7, 12)], TODAY)
    assert ytm is not None and ytw is not None
    assert ytw < ytm


# --- fallbacks ---------------------------------------------------------------

def test_approx_ytm_matches_exact_for_simple_bond():
    flows = [Flow("interest", date(2027, 7, 12), amount=100),
             Flow("interest", date(2028, 7, 12), amount=100),
             Flow("redemption", date(2028, 7, 12), amount=1000)]
    exact = yields.exact_ytm(950, _sched(flows), TODAY)
    approx = yields.approx_ytm(950, 10.0, 1000, date(2028, 7, 12), 1, TODAY)
    assert approx == pytest.approx(exact, abs=0.5)


def test_current_yield():
    assert yields.current_yield(950, 10, 1000) == pytest.approx(10.53, abs=0.01)


def test_xirr_simple():
    y = yields.xirr([(date(2025, 1, 1), -1000), (date(2026, 1, 1), 1100)])
    assert y == pytest.approx(10.0, abs=0.1)


def test_xirr_no_sign_change():
    assert yields.xirr([(date(2025, 1, 1), 100), (date(2026, 1, 1), 100)]) is None
