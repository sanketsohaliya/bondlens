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
