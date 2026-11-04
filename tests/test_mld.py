from datetime import date

from backend.analytics import mld, schedule, valuation


def test_is_mld_by_basis_or_rating():
    assert mld.is_mld("Variable - Index Linked", None)
    assert mld.is_mld("Variable- Commodity linked", None)
    assert mld.is_mld("Variable-Others", "CRISIL: PP-MLD AA/Stable")
    assert mld.is_mld("Fixed Interest", "CARE: PP-MLD  A+")
    assert not mld.is_mld("Fixed Interest", "CRISIL: AA/Stable")
    assert not mld.is_mld("Variable - Mibor Linked", None)
    assert not mld.is_mld(None, None)


def test_mld_zero_interest_row_is_labelled_not_treated_as_nothing():
    today = date(2026, 11, 4)
    rows = [{"event": "Interest", "due_date": date(2029, 1, 22), "record_date": date(2029, 1, 7), "amount_payable": 0},
            {"event": "Full Redemption", "due_date": date(2029, 1, 22), "record_date": date(2029, 1, 7), "amount_payable": 100000}]
    terms = {"coupon_basis": "Variable - Index Linked", "face_value": 100000,
             "interest_payment_frequency": "On Maturity", "redemption_date": date(2029, 1, 22)}
    sched = schedule.build("INE918K07PX1", rows, terms, today)
    coupon = [f for f in sched.flows if f.event == "interest"]
    assert len(coupon) == 1 and coupon[0].amount == 0
    assert [a["kind"] for a in coupon[0].adjustments] == ["index_linked"]
    # Same rows for a plain bond: no label.
    plain = schedule.build("X", rows, {**terms, "coupon_basis": "Fixed Interest"}, today)
    assert all(not f.adjustments for f in plain.flows if f.event == "interest")
    # Rating-only MLDs are caught when the caller knows the rating.
    rated = schedule.build("Y", rows, {**terms, "coupon_basis": "Variable-Others"}, today, market_linked=True)
    assert any(a["kind"] == "index_linked" for f in rated.flows for a in f.adjustments)


def test_valuation_marks_mld_yield_indicative_not_reliable():
    today = date(2026, 11, 4)
    rows = [{"event": "Interest", "due_date": date(2029, 1, 22), "record_date": None, "amount_payable": 0},
            {"event": "Full Redemption", "due_date": date(2029, 1, 22), "record_date": None, "amount_payable": 100000}]
    terms = {"coupon_basis": "Variable-Others", "face_value": 100000,
             "interest_payment_frequency": "On Maturity", "redemption_date": date(2029, 1, 22)}
    sched = schedule.build("Z", rows, terms, today, market_linked=True)
    priced = valuation.calculate(80000.0, sched, terms, today, market_linked=True)
    assert priced["ytm"] is not None and priced["ytm_reliable"] is False
    codes = [w["code"] for w in priced["warnings"]]
    assert "market_linked" in codes and "variable_coupon" not in codes
