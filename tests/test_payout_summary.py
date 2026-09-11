from copy import deepcopy
from datetime import date

from backend.analytics.compute import payout_summary

TODAY = date(2026, 9, 11)
ISIN = "INE148I07RZ0"
MATURITY = date(2027, 9, 27)
TERMS = {"face_value": 1000, "coupon_rate": 10, "redemption_date": MATURITY,
         "interest_payment_frequency": "Once a year", "allotment_date": date(2025, 9, 27)}


def rows():
    return [
        {"event": "Interest", "due_date": date(2026, 9, 27), "record_date": date(2026, 9, 12), "amount_payable": 100},
        {"event": "Interest", "due_date": MATURITY, "amount_payable": 100},
        {"event": "Redemption", "due_date": MATURITY, "amount_redeem": 1000},
    ]


def test_buyer_payout_is_gross_interest_plus_principal_per_unit():
    data = rows()
    before = deepcopy(data)
    summary = payout_summary(ISIN, data, TERMS, TODAY)
    assert summary["payout_total_snapshot"] == 1200
    assert summary["payout_state_snapshot"] == "known"
    assert summary["payout_as_of"] == TODAY
    assert summary["future_flows"] == 3
    assert summary["total_flows"] == 3
    assert data == before


def test_passed_record_date_excludes_future_money_for_new_buyer():
    data = rows()
    data[0]["record_date"] = TODAY
    summary = payout_summary(ISIN, data, TERMS, TODAY)
    assert summary["payout_total_snapshot"] == 1100
    assert summary["future_flows"] == 2


def test_payment_already_scheduled_early_is_not_pending_even_if_due_date_is_ahead():
    data = rows()
    data[0].update(record_date=None, payment_date=date(2026, 9, 10))
    summary = payout_summary(ISIN, data, TERMS, TODAY)
    assert summary["payout_total_snapshot"] == 1100
    assert summary["future_flows"] == 2


def test_zero_is_not_confused_with_missing_or_unbounded_schedules():
    assert payout_summary(ISIN, rows(), TERMS, date(2030, 1, 1))["payout_total_snapshot"] == 0
    assert payout_summary(ISIN, [], TERMS, TODAY)["payout_state_snapshot"] == "unavailable"
    for terms in ({**TERMS, "perpetual": True}, {**TERMS, "redemption_date": date(9999, 12, 31)}):
        result = payout_summary(ISIN, rows(), terms, TODAY)
        assert result["payout_total_snapshot"] is None
        assert result["payout_state_snapshot"] == "open_ended"


def test_incomplete_eligible_amounts_are_not_presented_as_a_known_subtotal():
    data = rows()
    data[0]["amount_payable"] = None
    result = payout_summary(ISIN, data, {**TERMS, "coupon_rate": None}, TODAY)
    assert result["payout_total_snapshot"] is None
    assert result["payout_state_snapshot"] == "incomplete"


def test_estimated_principal_and_variable_interest_are_qualified():
    data = rows()
    data[2]["amount_redeem"] = None
    result = payout_summary(ISIN, data, TERMS, TODAY)
    assert result["payout_total_snapshot"] == 1200
    assert result["payout_state_snapshot"] == "estimated"
    result = payout_summary(ISIN, rows(), {**TERMS, "coupon_basis": "Variable - Mibor Linked"}, TODAY)
    assert result["payout_state_snapshot"] == "estimated"


def test_staggered_and_accumulated_redemptions_are_counted_once():
    staggered = [
        {"event": "Interest", "due_date": date(2026, 10, 1), "amount_payable": 50},
        {"event": "Partial Redemption", "due_date": date(2026, 10, 1), "amount_redeem": 400},
        {"event": "Interest", "due_date": MATURITY, "amount_payable": 30},
        {"event": "Partial Redemption", "due_date": MATURITY, "amount_redeem": 600},
    ]
    assert payout_summary(ISIN, staggered, TERMS, TODAY)["payout_total_snapshot"] == 1080
    cumulative = [{"event": "Full Redemption", "due_date": MATURITY, "amount_redeem": 1600}]
    assert payout_summary(ISIN, cumulative, {**TERMS, "coupon_rate": None}, TODAY)["payout_total_snapshot"] == 1600


def test_issue_level_values_are_normalized_before_summing():
    data = rows()
    for row in data:
        key = "amount_payable" if row["event"] == "Interest" else "amount_redeem"
        row[key] *= 100
    result = payout_summary(ISIN, data, {**TERMS, "net_quantity": 100}, TODAY)
    assert result["payout_total_snapshot"] == 1200


def test_invalid_amounts_and_undated_obligations_withhold_the_total():
    for amount in (-1, float("inf"), float("nan")):
        data = rows()
        data[0]["amount_payable"] = amount
        assert payout_summary(ISIN, data, TERMS, TODAY)["payout_state_snapshot"] == "incomplete"
    data = rows() + [{"event": "Interest", "due_date": None, "amount_payable": 50}]
    assert payout_summary(ISIN, data, TERMS, TODAY)["payout_state_snapshot"] == "incomplete"


def test_invalid_source_amount_is_not_rebranded_as_a_complete_estimate_after_repair():
    data = rows()
    data[2]["amount_redeem"] = -10
    result = payout_summary(ISIN, data, {**TERMS, "redemption_type": "Full Redemption"}, TODAY)
    assert result["payout_state_snapshot"] == "incomplete"
    assert result["payout_total_snapshot"] is None


def test_invalid_record_date_sentinel_does_not_establish_buyer_entitlement():
    data = rows()
    data[0]["record_date"] = date(9999, 12, 31)
    assert payout_summary(ISIN, data, TERMS, TODAY)["payout_state_snapshot"] == "incomplete"


def test_snapshot_reader_hides_stale_values_without_mutating_the_snapshot():
    from backend.queries import payout_fields

    snapshot = {"payout_total_snapshot": 1200, "payout_state_snapshot": "known", "payout_as_of": TODAY}
    before = deepcopy(snapshot)
    assert payout_fields(snapshot, TODAY) == {
        "payout_total_per_unit": 1200, "payout_total_state": "known", "payout_as_of": TODAY,
    }
    for day in (date(2026, 9, 10), date(2026, 9, 12)):
        result = payout_fields(snapshot, day)
        assert result["payout_total_per_unit"] is None
        assert result["payout_total_state"] == "stale"
    assert snapshot == before
    assert payout_fields({}, TODAY)["payout_total_state"] == "unavailable"
    assert payout_fields({**snapshot, "payout_state_snapshot": "incomplete"}, TODAY)["payout_total_per_unit"] is None


def test_analytics_metadata_uses_payment_dates_without_changing_yield_inputs(monkeypatch):
    from backend import queries
    from backend.analytics import compute

    legacy = rows()
    current = deepcopy(legacy)
    current[0]["payment_date"] = date(2026, 9, 10)
    core = {ISIN: {"isin": ISIN, "on_nse": True, "on_bse": False, "coupon_rate": 10,
                   "face_value": 1000, "redemption_date": MATURITY,
                   "retail_trade": {"close": 1000, "trade_date": TODAY, "exchange": "NSE"}}}
    monkeypatch.setattr(queries, "explorer_core", lambda: core)
    monkeypatch.setattr(queries, "all_cashflows", lambda: {ISIN: legacy})
    monkeypatch.setattr(queries, "all_terms", lambda: {ISIN: TERMS})
    calls = []

    def payout_inputs(isins):
        calls.append(isins)
        return {ISIN: current}

    monkeypatch.setattr(queries, "cashflows_for", payout_inputs)
    monkeypatch.setattr(queries, "current_ratings", lambda: {})
    monkeypatch.setattr(queries, "rating_history", lambda: {})
    monkeypatch.setattr(queries, "defaulted_isins", lambda: set())
    monkeypatch.setattr(queries, "tech_delay_isins", lambda: set())
    early = compute.build_rows(TODAY)[0]
    assert early["payout_total_snapshot"] == 1100
    assert early["future_flows"] == 2
    assert calls == [[ISIN]]
    current[0]["payment_date"] = date(2026, 9, 25)
    later = compute.build_rows(TODAY)[0]
    assert later["payout_total_snapshot"] == 1200
    assert later["future_flows"] == 3
    metadata = {"payout_total_snapshot", "payout_state_snapshot", "payout_as_of", "future_flows", "total_flows", "computed_at"}
    assert {k: v for k, v in early.items() if k not in metadata} == {k: v for k, v in later.items() if k not in metadata}
