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


def test_mld_filed_premium_is_tagged_and_totalled():
    today = date(2026, 11, 4)
    # NSDL files one terminal payment above face: 3.08 L on 1 L.
    rows = [{"event": "Full Redemption", "due_date": date(2029, 6, 14), "record_date": date(2029, 5, 30), "amount_redeem": 308000}]
    terms = {"coupon_basis": "Variable - Index Linked", "face_value": 100000,
             "interest_payment_frequency": "On Maturity", "redemption_date": date(2029, 6, 14),
             "redemption_type": "Full"}
    sched = schedule.build("INE0K0Y07013", rows, terms, today, market_linked=True)
    interest = [f for f in sched.flows if f.event == "interest"]
    assert len(interest) == 1 and interest[0].amount == 208000
    assert [a["kind"] for a in interest[0].adjustments][-1] == "index_linked_premium"
    assert sched.mld_premium == 208000
    priced = valuation.calculate(190000.0, sched, terms, today, market_linked=True)
    ml = next(w for w in priced["warnings"] if w["code"] == "market_linked")
    assert "best case" in ml["message"] and ml["premium"] == 208000
    # Nothing beyond face: no premium, the "no coupon" wording.
    rows0 = [{"event": "Interest", "due_date": date(2029, 1, 22), "record_date": None, "amount_payable": 0},
             {"event": "Full Redemption", "due_date": date(2029, 1, 22), "record_date": None, "amount_payable": 100000}]
    s0 = schedule.build("INE918K07PX1", rows0, {**terms, "redemption_date": date(2029, 1, 22)}, today, market_linked=True)
    assert s0.mld_premium is None
    p0 = valuation.calculate(80000.0, s0, terms, today, market_linked=True)
    assert "no coupon" in next(w for w in p0["warnings"] if w["code"] == "market_linked")["message"]
