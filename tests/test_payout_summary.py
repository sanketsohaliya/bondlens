from contextlib import contextmanager
from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

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


def test_snapshot_records_the_next_boundary_that_could_change_the_buyer_total():
    # rows(): record 12 Sep, due 27 Sep, then maturity 27 Sep 2027. Earliest future boundary is the record date.
    assert payout_summary(ISIN, rows(), TERMS, TODAY)["payout_valid_until"] == date(2026, 9, 12)
    # Once the record date has passed, the next boundary is the payment (due) date.
    assert payout_summary(ISIN, rows(), TERMS, date(2026, 9, 12))["payout_valid_until"] == date(2026, 9, 27)
    # A planned payment date earlier than the due date is the real boundary.
    data = rows()
    data[0].update(record_date=None, payment_date=date(2026, 9, 20))
    assert payout_summary(ISIN, data, TERMS, TODAY)["payout_valid_until"] == date(2026, 9, 20)
    # Nothing ahead → no boundary; the snapshot stays valid indefinitely.
    assert payout_summary(ISIN, rows(), TERMS, date(2030, 1, 1))["payout_valid_until"] is None
    # A sentinel record date is never a boundary (and the row is incomplete anyway).
    data = rows()
    data[0]["record_date"] = date(9999, 12, 31)
    assert payout_summary(ISIN, data, TERMS, TODAY)["payout_valid_until"] == date(2026, 9, 27)


def test_snapshot_reader_withholds_only_when_a_boundary_has_actually_passed():
    from backend.queries import payout_fields

    snapshot = {"payout_total_snapshot": 1200, "payout_state_snapshot": "known",
                "payout_as_of": TODAY, "payout_valid_until": date(2026, 9, 18)}
    before = deepcopy(snapshot)
    fresh = {"payout_total_per_unit": 1200, "payout_total_state": "known",
             "payout_as_of": TODAY, "payout_valid_until": date(2026, 9, 18)}
    # Same day: always current.
    assert payout_fields(snapshot, TODAY) == fresh
    # Overnight, before the boundary: provably identical → still shown. This is the fix.
    assert payout_fields(snapshot, date(2026, 9, 12)) == fresh
    assert payout_fields(snapshot, date(2026, 9, 17)) == fresh
    # On/after the boundary: genuinely changed → withheld.
    for day in (date(2026, 9, 18), date(2026, 9, 25)):
        result = payout_fields(snapshot, day)
        assert result["payout_total_per_unit"] is None
        assert result["payout_total_state"] == "stale"
    # A snapshot dated *after* today (clock skew) is not treated as stale either.
    assert payout_fields(snapshot, date(2026, 9, 10))["payout_total_state"] == "known"
    # No boundary at all → never stale.
    assert payout_fields({**snapshot, "payout_valid_until": None}, date(2026, 12, 1))["payout_total_state"] == "known"
    # Row predating migration 0020 (column absent) → not stale.
    legacy = {k: v for k, v in snapshot.items() if k != "payout_valid_until"}
    assert payout_fields(legacy, date(2026, 9, 12))["payout_total_state"] == "known"
    # Boundary is today and snapshot is today → current (compute already accounted for it).
    assert payout_fields({**snapshot, "payout_valid_until": TODAY}, TODAY)["payout_total_state"] == "known"
    assert snapshot == before
    assert payout_fields({}, TODAY)["payout_total_state"] == "unavailable"
    assert payout_fields({**snapshot, "payout_state_snapshot": "incomplete"}, TODAY)["payout_total_per_unit"] is None


@pytest.mark.parametrize(
    "payment_shift,coupon_payment,record_offset,expected_ytm",
    [
        pytest.param(None, None, None, 10.0, id="missing-payment-dates"),
        pytest.param(0, None, None, 10.0, id="same-payment-dates"),
        pytest.param(3, None, None, 9.91, id="later-payment-dates"),
        pytest.param(-3, None, None, 10.09, id="earlier-payment-dates"),
        pytest.param(None, -1, None, 10.0, id="early-coupon-no-record-date"),
        pytest.param(None, 0, None, 10.0, id="coupon-payment-today"),
        pytest.param(None, -1, -10, 10.0, id="early-coupon-record-passed"),
        pytest.param(None, 1, 0, 10.0, id="future-coupon-record-today"),
    ],
)
def test_explorer_and_detail_use_same_payment_dates(
    monkeypatch, payment_shift, coupon_payment, record_offset, expected_ytm,
):
    from backend import app as api_module
    from backend import db, queries
    from backend.analytics import compute

    maturity = TODAY + timedelta(days=365)
    terms = {**TERMS, "redemption_date": maturity,
             "day_count_convention": "Actual/365"}
    data = [
        {"isin": ISIN, "event": "Interest", "due_date": maturity, "amount_payable": 100},
        {"isin": ISIN, "event": "Redemption", "due_date": maturity, "amount_redeem": 1000},
    ]
    if payment_shift is not None:
        for row in data:
            row["payment_date"] = maturity + timedelta(days=payment_shift)
    if coupon_payment is not None:
        data.insert(0, {
            "isin": ISIN, "event": "Interest", "due_date": TODAY + timedelta(days=2),
            "payment_date": TODAY + timedelta(days=coupon_payment),
            "record_date": TODAY + timedelta(days=record_offset) if record_offset is not None else None,
            "amount_payable": 100,
        })
    before = deepcopy(data)

    def execute(statement, params=None):
        projection, separator, _ = statement.partition(" FROM bond_cashflows")
        assert separator and projection.startswith("SELECT ")
        assert params is None or params in ((ISIN,), ([ISIN],))
        columns = [column.strip() for column in projection.removeprefix("SELECT ").split(",")]
        result = [{column: row.get(column) for column in columns} for row in data]
        return SimpleNamespace(fetchall=lambda: result)

    @contextmanager
    def connection():
        yield SimpleNamespace(execute=execute)

    class FixedDate(date):
        @classmethod
        def today(cls):
            return TODAY

    core = {ISIN: {"isin": ISIN, "on_nse": True, "on_bse": False, "coupon_rate": 10,
                   "face_value": 1000, "redemption_date": maturity,
                   "retail_trade": {"close": 1000, "trade_date": TODAY, "exchange": "NSE"}}}
    monkeypatch.setattr(db, "_conn", connection)
    monkeypatch.setattr(api_module, "date", FixedDate)
    monkeypatch.setattr(queries, "explorer_core", lambda: core)
    monkeypatch.setattr(queries, "all_terms", lambda: {ISIN: terms})
    monkeypatch.setattr(queries, "current_ratings", lambda: {})
    monkeypatch.setattr(queries, "rating_history", lambda *args: {})
    monkeypatch.setattr(queries, "defaulted_isins", lambda: set())
    monkeypatch.setattr(queries, "tech_delay_isins", lambda: set())
    monkeypatch.setattr(queries, "bond_row", lambda isin: {"isin": isin, "issuer_name": None})
    monkeypatch.setattr(queries, "bond_terms", lambda isin: terms)
    monkeypatch.setattr(queries, "bond_extras", lambda isin: {"defaults": [], "technical_defaults": []})
    monkeypatch.setattr(queries, "bond_trustee", lambda isin: None)
    monkeypatch.setattr(api_module.kite, "kite_url", lambda *args, **kwargs: None)

    explorer = compute.build_rows(TODAY)[0]
    monkeypatch.setattr(queries, "analytics_row", lambda *args: explorer)
    detail = api_module.bond_detail(ISIN, price=None)

    assert explorer["ltp"] == detail["ltp"] == 1000
    assert explorer["payout_as_of"] == detail["as_of"] == TODAY
    assert detail["ytm_exact"] == expected_ytm
    assert explorer["ytm"] == detail["ytm"] == expected_ytm
    assert explorer["ytm_method"] == "exact"
    assert explorer["duration"] == detail["duration"]
    assert explorer["payout_total_snapshot"] == detail["future_total_per_unit"] == 1100
    assert explorer["future_flows"] == sum(f["future"] for f in detail["cashflows"]) == 2
    assert data == before
