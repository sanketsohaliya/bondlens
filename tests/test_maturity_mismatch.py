"""A cashflow schedule that contradicts the stated maturity must flag the
yield and keep the bond out of alert thresholds (Kosamattam INE403Q07CD1:
schedule redeems Sep 2027, terms say Sep 2028, yield doubled)."""
from datetime import date, timedelta

from backend import opportunities as opp
from backend.analytics import valuation
from backend.analytics.schedule import Flow, Schedule, maturity_mismatch

TODAY = date(2026, 10, 15)


def cumulative(redeem_on: date) -> Schedule:
    return Schedule("INE403Q07CD1", "cumulative",
                    [Flow("interest", redeem_on, amount=1000), Flow("redemption", redeem_on, amount=1000)],
                    face_value=1000, redemption_matches_face=True, periods_per_year=1,
                    day_count="act/365", maturity_date=redeem_on)


def test_mismatch_detected_only_beyond_tolerance_and_never_for_perpetuals():
    stated = date(2028, 9, 29)
    assert maturity_mismatch(cumulative(date(2027, 9, 29)), {"redemption_date": stated}) \
        == (date(2027, 9, 29), stated)
    assert maturity_mismatch(cumulative(stated + timedelta(days=30)), {"redemption_date": stated}) is None
    assert maturity_mismatch(cumulative(stated - timedelta(days=46)), {"redemption_date": stated}) is not None
    assert maturity_mismatch(cumulative(stated), {}) is None
    assert maturity_mismatch(cumulative(stated), {"redemption_date": date(9999, 12, 31)}) is None
    perp = Schedule("X", "perpetual", [Flow("interest", stated, amount=50)], perpetual=True)
    assert maturity_mismatch(perp, {"redemption_date": date(2030, 1, 1)}) is None


def test_valuation_flags_the_yield_and_withholds_totals():
    sched = cumulative(date(2027, 9, 29))
    terms = {"redemption_date": date(2028, 9, 29)}
    v = valuation.calculate(1630.85, sched, terms, TODAY)
    assert v["ytm"] is not None and v["ytm"] > 20          # the schedule's (wrong) yield still shown
    [note] = [w for w in v["warnings"] if w["code"] == "maturity_mismatch"]
    assert note["severity"] == "error" and "29 Sep 2027" in note["message"] and "29 Sep 2028" in note["message"]
    assert note["schedule_date"] == "2027-09-29" and note["stated_date"] == "2028-09-29"
    assert v["ytm_reliable"] is False and v["cashflows_complete"] is False and v["gross_payout"] is None
    ok = valuation.calculate(1630.85, cumulative(date(2028, 9, 29)), terms, TODAY)
    assert ok["ytm_reliable"] is True and all(w["code"] != "maturity_mismatch" for w in ok["warnings"])


def test_no_price_threshold_for_a_mismatched_schedule():
    terms = {"redemption_date": date(2028, 9, 29)}
    assert opp.price_for_yield(cumulative(date(2027, 9, 29)), terms, 15.0, TODAY) is None
    assert opp.price_for_yield(cumulative(date(2028, 9, 29)), terms, 15.0, TODAY) is not None
