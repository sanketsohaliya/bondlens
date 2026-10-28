import json
from datetime import date, timedelta

import pytest

from backend.analytics import valuation
from backend.analytics.schedule import Flow, Schedule

TODAY = date(2026, 7, 12)


def schedule(estimated=False):
    end = TODAY + timedelta(days=365)
    return Schedule("TEST", "plain", [Flow("interest", end, amount=100, estimated=estimated),
                                      Flow("redemption", end, amount=1000)], face_value=1000,
                    periods_per_year=1, day_count="act/365", redemption_matches_face=True,
                    maturity_date=end)


def test_price_carries_the_actual_warning_and_calculation_context():
    result = valuation.calculate(650, schedule(), {}, TODAY)
    assert result["ytm"] == 69.23
    assert result["ytm_method"] == "exact"
    assert not result["ytm_reliable"]
    assert "yield_range" in {w["code"] for w in result["warnings"]}
    assert result["as_of"] == str(TODAY)
    assert result["calculation_version"] == valuation.VERSION
    assert result["periods_per_year"] == 1
    assert result["cashflows_complete"]


@pytest.mark.parametrize("price", [1000, 950])
def test_otc_difference_is_context_not_an_unreliable_yield(price):
    baseline = valuation.calculate(price, schedule(), {}, TODAY)
    compared = valuation.calculate(price, schedule(), {}, TODAY, otc_yield=5, otc_trade_date=TODAY)
    assert compared["ytm"] == baseline["ytm"]
    assert compared["ytm_method"] == baseline["ytm_method"]
    assert compared["ytm_reliable"] is True
    [note] = compared["warnings"]
    assert note["code"] == "otc_difference" and note["severity"] == "info"
    assert "5.00%" in note["message"] and str(TODAY) in note["message"]


def test_otc_context_does_not_suppress_an_estimated_payment_caution():
    result = valuation.calculate(1000, schedule(estimated=True), {}, TODAY,
                                 otc_yield=5, otc_trade_date=TODAY)
    assert result["ytm_reliable"] is False
    assert {note["code"] for note in result["warnings"]} == {"otc_difference", "estimated_payments"}


def test_estimates_are_not_lost_at_a_different_price():
    result = valuation.calculate(1000, schedule(estimated=True), {}, TODAY)
    assert result["ytm_method"] == "estimated" and result["estimated"]
    assert "estimated_payments" in {w["code"] for w in result["warnings"]}


def test_short_horizon_shows_rupee_gain_even_inside_normal_yield_band():
    sched = Schedule("TEST", "zero_coupon", [Flow("redemption", TODAY + timedelta(days=5), amount=1000)],
                     face_value=1000, redemption_matches_face=True, day_count="act/365")
    result = valuation.calculate(999, sched, {}, TODAY)
    assert result["gross_gain"] == 1
    assert result["holding_period_return"] == pytest.approx(100 / 999)
    assert result["ytm"] == 7.58
    assert "short_horizon" in {w["code"] for w in result["warnings"]}


@pytest.mark.parametrize("retail,otc,gap,direction", [
    (18.26, 12.26, 6.0, "above"),
    (9.10, 12.26, -3.16, "below"),
])
def test_otc_difference_falls_back_to_the_yield_gap_without_a_price(retail, otc, gap, direction):
    [note] = valuation.cautions(retail, TODAY + timedelta(days=365), TODAY,
                                otc_yield=otc, otc_trade_date=TODAY)
    assert note["code"] == "otc_difference"
    assert note["otc_yield"] == otc
    # Stored inside the JSONB valuation column, so it must already be JSON-safe.
    assert note["otc_trade_date"] == TODAY.isoformat()
    json.dumps(note)
    assert note["gap"] == gap
    assert "otc_price" not in note and "price_gap" not in note
    assert note["message"].startswith(f"Retail yield {retail:.2f}% is {abs(gap):.2f} pts {direction}")


@pytest.mark.parametrize("retail_px,otc_px,price_gap,pct,word", [
    (9425.0, 9950.0, -525.0, -5.28, "cheaper"),   # Kanakadurga: retail below institutional
    (10300.0, 9950.0, 350.0, 3.52, "more"),        # retail dearer than institutional
])
def test_otc_difference_states_the_rupee_gap_in_the_units_of_the_ltp(retail_px, otc_px, price_gap, pct, word):
    [note] = valuation.cautions(18.26, TODAY + timedelta(days=365), TODAY,
                                otc_yield=12.26, otc_trade_date=TODAY,
                                otc_price=otc_px, price=retail_px)
    assert note["otc_price"] == otc_px and note["retail_price"] == retail_px
    assert note["price_gap"] == price_gap
    assert note["price_gap_pct"] == pct
    assert note["message"].startswith(f"Institutions paid about Rs {otc_px:,.0f} per unit")
    assert f"Rs {abs(price_gap):,.0f} ({abs(pct):.1f}%) {word}" in note["message"]
    assert "probably stale" not in note["message"] and "order book" not in note["message"]
    json.dumps(note)


def test_calculate_derives_the_otc_rupee_price_from_the_clean_percentage():
    # 99.99% of a 1,000 face = 999.90, plus one day of a 100 coupon accrued
    # from allotment (100/365 = 0.27) -> 1,000.17 dirty.
    sched = schedule()
    sched.allotment_date = TODAY - timedelta(days=1)
    result = valuation.calculate(950.0, sched, {}, TODAY, otc_yield=5, otc_trade_date=TODAY,
                                 otc_price_pct=99.99)
    [note] = [w for w in result["warnings"] if w["code"] == "otc_difference"]
    assert note["otc_price"] == 1000.17
    assert note["retail_price"] == 950.0
    assert note["price_gap"] == -50.17 and "cheaper" in note["message"]


def test_calculate_falls_back_when_accrued_interest_cannot_be_computed():
    # No allotment date and no prior coupon: accrued is unknowable, so the
    # note carries the yield comparison only rather than a guessed price.
    result = valuation.calculate(950.0, schedule(), {}, TODAY, otc_yield=5, otc_trade_date=TODAY,
                                 otc_price_pct=99.99)
    [note] = [w for w in result["warnings"] if w["code"] == "otc_difference"]
    assert "otc_price" not in note
    assert note["message"].startswith("Retail yield")


def test_otc_within_tolerance_produces_no_comparison_note():
    assert valuation.cautions(12.0, TODAY + timedelta(days=365), TODAY,
                              otc_yield=12.26, otc_trade_date=TODAY) == []


def test_future_otc_print_is_not_called_a_fresh_comparison():
    warnings = valuation.cautions(10, TODAY + timedelta(days=365), TODAY,
                                  otc_yield=20, otc_trade_date=TODAY + timedelta(days=1))
    assert [w["code"] for w in warnings] == ["future_otc_date"]


def test_future_benchmark_remains_flagged_without_a_yield():
    warnings = valuation.cautions(None, None, TODAY, otc_trade_date=TODAY + timedelta(days=1))
    assert [warning["code"] for warning in warnings] == ["future_otc_date"]


def test_price_endpoint_reprices_arbitrary_limits_and_rejects_nonfinite_prices(monkeypatch):
    from fastapi.testclient import TestClient

    from backend import app as api_module
    from backend import auth, queries

    now = date.today()
    end = now + timedelta(days=365)
    monkeypatch.setattr(queries, "bond_row", lambda isin: {"isin": isin})
    monkeypatch.setattr(queries, "analytics_row", lambda *args: {})
    monkeypatch.setattr(queries, "exchange_notice_rows", lambda isins=None, **k: {})
    monkeypatch.setattr(queries, "bond_terms", lambda isin: {"face_value": 1000, "coupon_rate": 10,
                        "interest_payment_frequency": "Annual", "redemption_date": end,
                        "day_count_convention": "Actual/365"})
    monkeypatch.setattr(queries, "bond_cashflows", lambda isin: [
        {"event": "Interest", "due_date": end, "amount_payable": 100},
        {"event": "Redemption", "due_date": end, "amount_redeem": 1000}])
    api_module.app.dependency_overrides[auth.require_user] = lambda: 1
    try:
        client = TestClient(api_module.app)
        response = client.get("/api/bonds/TEST/valuation?price=900")
        assert response.status_code == 200
        result = response.json()
        assert result["price"] == 900 and result["ytm"] == 22.22
        assert result["as_of"] == str(now) and result["ytm_method"] == "exact"
        assert client.get("/api/bonds/TEST/valuation?price=nan").status_code == 422
        assert client.get("/api/bonds/TEST/valuation?price=0").status_code == 422
        tiny = client.get("/api/bonds/TEST/valuation?price=1e-320")
        assert tiny.status_code == 200 and tiny.json()["holding_period_return"] is None
    finally:
        api_module.app.dependency_overrides.clear()


def test_perpetual_uses_the_same_explicit_call_scenario():
    end = TODAY + timedelta(days=365)
    sched = Schedule("TEST", "perpetual", [Flow("interest", end, amount=100)],
                     face_value=1000, periods_per_year=1, day_count="act/365")
    terms = {"call_indicator": "Yes", "call_specified_dates": str(end)}
    result = valuation.calculate(1000, sched, terms, TODAY)
    assert result["ytm"] == 10
    assert result["yield_type"] == result["ytm_method"] == "ytc"
    assert result["gross_payout"] == 1100
    assert "call_scenario" in {w["code"] for w in result["warnings"]}


def test_price_age_is_information_not_a_reliability_caution():
    # 37 days since the last trade: the note is present, but only as context.
    result = valuation.calculate(950.0, schedule(), {}, TODAY,
                                 price_date=TODAY - timedelta(days=37))
    [note] = [w for w in result["warnings"] if w["code"] == "old_price"]
    assert note["severity"] == "info"
    assert "37 days old" in note["message"]
    assert result["ytm_reliable"] is True


def test_a_genuine_calculation_caution_still_marks_the_yield_unreliable():
    # Same stale price, but an out-of-range yield: that one is a caution.
    result = valuation.calculate(650.0, schedule(), {}, TODAY,
                                 price_date=TODAY - timedelta(days=37))
    codes = {w["code"]: w["severity"] for w in result["warnings"]}
    assert codes["old_price"] == "info"
    assert codes["yield_range"] == "caution"
    assert result["ytm_reliable"] is False
