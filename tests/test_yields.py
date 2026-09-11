"""Yield/accrued/duration math tests with hand-computed cases."""
from copy import deepcopy
from datetime import date

import pytest

from backend.analytics import daycount, yields
from backend.analytics.schedule import Flow, Schedule

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

def test_accrued_first_period_from_allotment():
    # No coupon paid yet (9EFSL27 case): accrue from the allotment date.
    flows = [Flow("interest", date(2026, 7, 24), amount=90),
             Flow("interest", date(2027, 7, 24), amount=90),
             Flow("redemption", date(2027, 7, 24), amount=1000)]
    s = _sched(flows)
    s.allotment_date = date(2025, 7, 24)
    acc = yields.accrued_interest(s, TODAY)
    # 353/365 of the first coupon has accrued by 12 Jul 2026.
    assert acc == pytest.approx(90 * 353 / 365, abs=1.0)


def test_accrued_first_period_without_allotment_is_none():
    flows = [Flow("interest", date(2026, 7, 24), amount=90),
             Flow("redemption", date(2026, 7, 24), amount=1000)]
    assert yields.accrued_interest(_sched(flows), TODAY) is None


def test_accrued_midway():
    flows = [Flow("interest", date(2026, 1, 12), amount=100),
             Flow("interest", date(2027, 1, 12), amount=100),
             Flow("redemption", date(2027, 1, 12), amount=1000)]
    acc = yields.accrued_interest(_sched(flows), TODAY)
    # 181/365 of the way through the coupon period
    assert acc == pytest.approx(100 * 181 / 365, abs=1.0)


def test_accrued_negative_in_ex_interest_window():
    # 13LLDL29 case: next coupon due 31 Jul with record date 16 Jul, today
    # 12 Jul... shift today past the record date. A buyer settling then
    # misses the coupon -> negative accrued for the unearned remainder.
    today = date(2026, 7, 20)
    flows = [Flow("interest", date(2026, 6, 30), amount=100),
             Flow("interest", date(2026, 7, 31), record_date=date(2026, 7, 16),
                  amount=100),
             Flow("interest", date(2027, 7, 31), amount=100),
             Flow("redemption", date(2027, 7, 31), amount=1000)]
    acc = yields.accrued_interest(_sched(flows), today)
    # 11 of 31 days remain unearned -> -100 * 11/31
    assert acc == pytest.approx(-100 * 11 / 31, abs=1.0)


def test_clean_price_above_dirty_when_ex_interest():
    today = date(2026, 7, 20)
    flows = [Flow("interest", date(2026, 6, 30), amount=100),
             Flow("interest", date(2026, 7, 31), record_date=date(2026, 7, 16),
                  amount=100),
             Flow("redemption", date(2027, 7, 31), amount=1000)]
    s = _sched(flows)
    cp = yields.clean_price(1000, s, today)
    assert cp is not None and cp > 1000


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


def test_implied_coupon_cumulative():
    # 1000 face doubles to 2001 over ~6.55y -> ~11.2% effective annual.
    flows = [Flow("interest", date(2031, 10, 30), amount=1001),
             Flow("redemption", date(2031, 10, 30), amount=1000)]
    s = _sched(flows, shape="cumulative")
    s.allotment_date = date(2025, 4, 10)
    r = yields.implied_coupon(s)
    assert r == pytest.approx(11.2, abs=0.2)


def test_implied_coupon_zero_coupon_uses_issue_price():
    # Redeems at par: the return is the issue discount.
    flows = [Flow("redemption", date(2028, 7, 12), amount=1000)]
    s = _sched(flows, shape="zero_coupon")
    s.allotment_date = date(2026, 7, 12)
    assert yields.implied_coupon(s) is None  # no issue price known
    r = yields.implied_coupon(s, issue_price=826.45)
    assert r == pytest.approx(10.0, abs=0.1)


def test_implied_coupon_only_for_terminal_shapes():
    flows = [Flow("interest", date(2027, 7, 12), amount=100),
             Flow("redemption", date(2027, 7, 12), amount=1000)]
    s = _sched(flows, shape="plain")
    s.allotment_date = date(2026, 7, 12)
    assert yields.implied_coupon(s) is None


def test_implied_coupon_requires_allotment_date():
    flows = [Flow("interest", date(2031, 10, 30), amount=1001),
             Flow("redemption", date(2031, 10, 30), amount=1000)]
    assert yields.implied_coupon(_sched(flows, shape="cumulative")) is None


def test_xirr_simple():
    y = yields.xirr([(date(2025, 1, 1), -1000), (date(2026, 1, 1), 1100)])
    assert y == pytest.approx(10.0, abs=0.1)


def test_xirr_no_sign_change():
    assert yields.xirr([(date(2025, 1, 1), 100), (date(2026, 1, 1), 100)]) is None


def test_actual_actual_isda_is_additive_and_monotonic_across_leap_day():
    start = date(2026, 1, 1)
    boundary = date(2028, 1, 1)
    ends = [date(2028, 2, 28), date(2028, 2, 29), date(2028, 3, 1)]
    fractions = [daycount.year_frac(start, end) for end in ends]
    assert fractions == sorted(fractions)
    for end, fraction in zip(ends, fractions, strict=True):
        assert fraction == pytest.approx(2 + (end - boundary).days / 366, abs=1e-12)
        assert fraction == pytest.approx(
            daycount.year_frac(start, boundary) + daycount.year_frac(boundary, end), abs=1e-12)
    assert daycount.year_frac(date(2028, 3, 1), date(2028, 4, 1)) == pytest.approx(31 / 366)
    assert daycount.year_frac(date(9999, 1, 1), date(9999, 12, 31)) == pytest.approx(364 / 365)


def test_european_30_360_is_not_bond_basis():
    start, end = date(2026, 1, 29), date(2026, 1, 31)
    assert daycount.year_frac(start, end, daycount.normalize("30E/360")) == pytest.approx(1 / 360)
    assert daycount.year_frac(start, end, daycount.normalize("30/360")) == pytest.approx(2 / 360)
    assert daycount.normalize("Actual/Actual (ISDA)") == daycount.ACT_ACT


def test_unsupported_day_count_does_not_silently_become_actual_actual():
    convention = daycount.normalize("Actual/Actual (ICMA)")
    assert convention != daycount.ACT_ACT
    with pytest.raises(ValueError):
        daycount.year_frac(TODAY, date(2027, 7, 12), convention)


def test_exact_yield_does_not_skip_an_unknown_future_coupon():
    flows = [Flow("interest", date(2027, 7, 12), amount=None),
             Flow("redemption", date(2027, 7, 12), amount=1000)]
    assert yields.exact_ytm(1000, _sched(flows), TODAY) is None


def test_missing_excluded_coupon_does_not_poison_buyers_yield():
    flows = [Flow("interest", date(2026, 7, 20), record_date=TODAY, amount=None),
             Flow("interest", date(2027, 7, 12), amount=100),
             Flow("redemption", date(2027, 7, 12), amount=1000)]
    assert yields.exact_ytm(1000, _sched(flows, dc=daycount.ACT_365), TODAY) == 10.0


def test_perpetual_cannot_be_priced_to_the_end_of_its_published_window():
    s = _sched([Flow("interest", date(2027 + i, 7, 12), amount=100)
                for i in range(10)], shape="perpetual")
    assert yields.exact_ytm(1000, s, TODAY) is None


def test_call_yields_include_interim_principal_without_mutating_schedule():
    start = date(2025, 1, 1)
    flows = [Flow("interest", date(2026, 1, 1), amount=100),
             Flow("redemption", date(2026, 1, 1), amount=500),
             Flow("interest", date(2027, 1, 1), amount=50),
             Flow("interest", date(2028, 1, 1), amount=50),
             Flow("redemption", date(2028, 1, 1), amount=500)]
    s = _sched(flows, dc=daycount.ACT_365, shape="staggered")
    call = [date(2027, 1, 1)]
    before = deepcopy(s.flows)
    assert yields.yield_to_call(1000, s, call, start) == 10.0
    assert yields.yield_to_worst(1000, s, call, start) == 10.0
    assert s.flows == before


def test_call_does_not_subtract_historical_principal_from_current_face_twice():
    start = date(2025, 1, 1)
    flows = [Flow("redemption", date(2024, 1, 1), amount=500),
             Flow("interest", date(2026, 1, 1), amount=50),
             Flow("redemption", date(2027, 1, 1), amount=500)]
    s = _sched(flows, face=500, dc=daycount.ACT_365, shape="staggered")
    assert yields.yield_to_call(500, s, [date(2026, 1, 1)], start) == 10.0


def test_call_on_partial_redemption_date_redeems_principal_once():
    start = date(2025, 1, 1)
    flows = [Flow("interest", date(2026, 1, 1), amount=100),
             Flow("redemption", date(2026, 1, 1), amount=500),
             Flow("interest", date(2027, 1, 1), amount=50),
             Flow("redemption", date(2027, 1, 1), amount=500)]
    s = _sched(flows, dc=daycount.ACT_365, shape="staggered")
    assert yields.yield_to_call(1000, s, [date(2026, 1, 1)], start) == 10.0


def test_call_respects_record_date_exclusion_of_interim_principal():
    start = date(2025, 1, 1)
    flows = [Flow("interest", date(2026, 1, 1), amount=100),
             Flow("redemption", date(2026, 1, 1), record_date=date(2024, 12, 31), amount=500),
             Flow("interest", date(2027, 1, 1), amount=50),
             Flow("redemption", date(2028, 1, 1), amount=500)]
    s = _sched(flows, dc=daycount.ACT_365, shape="staggered")
    price = 100 / 1.1 + 550 / 1.1 ** 2
    assert yields.yield_to_call(price, s, [date(2027, 1, 1)], start) == 10.0


def test_unknown_call_cashflow_cannot_silently_fall_back_to_maturity_yield():
    flows = [Flow("interest", date(2027, 7, 12), amount=100),
             Flow("interest", date(2028, 7, 12), amount=100),
             Flow("redemption", date(2028, 7, 12), amount=1000)]
    s = _sched(flows)
    assert yields.exact_ytm(1000, s, TODAY) is not None
    assert yields.yield_to_worst(1000, s, [date(2027, 1, 12)], TODAY) is None


@pytest.mark.parametrize("text", ["Wednesday, 8 September, 2027", "8th September 2027", "2027-09-08", "08-September-2027"])
def test_parse_explicit_call_date_formats(text):
    assert yields.parse_call_dates({"call_indicator": "Yes", "call_specified_dates": text}) == [date(2027, 9, 8)]


def test_notice_deadline_is_not_a_call_payment_date():
    assert yields.parse_call_dates({"call_indicator": "Yes", "call_deadline": "08-09-2027"}) == []


def test_approximation_does_not_treat_unknown_coupon_as_zero():
    assert yields.approx_ytm(900, None, 1000, date(2027, 7, 12), today=TODAY) is None
    assert yields.approx_ytm(900, 0, 1000, date(2027, 7, 12), today=TODAY) == pytest.approx(11.11)


def test_call_preserves_published_full_redemption_payment_date():
    due, paid = date(2027, 7, 12), date(2027, 7, 15)
    flows = [Flow("interest", due, amount=100, payment_date=paid),
             Flow("redemption", due, amount=1000, payment_date=paid)]
    s = _sched(flows, dc=daycount.ACT_365)
    assert yields.yield_to_call(1000, s, [due], TODAY) == 9.91


def test_call_with_ambiguous_principal_basis_is_unavailable():
    flows = [Flow("interest", date(2027, 7, 12), amount=100),
             Flow("redemption", date(2028, 7, 12), amount=500)]
    assert yields.yield_to_call(1000, _sched(flows), [date(2027, 7, 12)], TODAY) is None


def test_first_invalid_call_is_not_silently_replaced_by_a_later_call():
    flows = [Flow("interest", date(2027, 7, 12), amount=None),
             Flow("interest", date(2028, 7, 12), amount=100),
             Flow("redemption", date(2028, 7, 12), amount=1000)]
    assert yields.yield_to_call(1000, _sched(flows), [date(2027, 7, 12), date(2028, 7, 12)], TODAY) is None


def test_empty_perpetual_schedule_cannot_invent_a_principal_only_call_yield():
    s = _sched([], shape="perpetual")
    assert yields.yield_to_call(1000, s, [date(2027, 7, 12)], TODAY) is None


def test_repayment_before_call_with_no_principal_remaining_requires_review():
    flows = [Flow("interest", date(2027, 7, 11), amount=100),
             Flow("redemption", date(2027, 7, 11), amount=1000)]
    assert yields.yield_to_call(1000, _sched(flows), [date(2027, 7, 12)], TODAY) is None
