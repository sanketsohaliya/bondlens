"""NSDL lists only the repayment on dates that also pay a coupon
(INE0P6G07194): the coupon is added from the rate and principal timeline,
the yield prices exactly, and the note explains it in plain words."""
from datetime import date, timedelta

from backend.analytics import daycount, schedule, valuation

TODAY = date(2026, 10, 28)
TERMS = {"face_value": 10000, "coupon_rate": 11, "coupon_type": "Simple",
         "interest_payment_frequency": "Twelve times a year", "day_count_convention": "Actual/actual",
         "allotment_date": date(2026, 9, 17), "redemption_date": date(2029, 3, 17),
         "redemption_type": "Partial Redemption By Face Value"}


def hella_rows(*, drop_coupons_on_repayment=True, shift_final=0):
    rows = []
    d = date(2026, 10, 17)
    while d <= date(2029, 3, 17):
        repay = {date(2028, 9, 17): 3000, date(2028, 12, 17): 3000, date(2029, 3, 17): 4000}.get(d)
        if repay:
            due = d + timedelta(days=shift_final) if d == date(2029, 3, 17) else d
            rows.append({"event": "Full Redemption" if repay == 4000 else "Partial Redemption",
                         "due_date": due, "record_date": due - timedelta(days=15), "payment_date": due,
                         "amount_payable": repay, "amount_redeem": None, "amount_outstanding": None})
            if drop_coupons_on_repayment:
                d = date(d.year + (d.month == 12), d.month % 12 + 1, 17)
                continue
        outstanding = 10000 - (3000 if d > date(2028, 9, 17) else 0) - (3000 if d > date(2028, 12, 17) else 0)
        rows.append({"event": "Interest", "due_date": d, "record_date": d - timedelta(days=15), "payment_date": d,
                     "amount_payable": round(outstanding * 0.11 * 30 / 365, 2), "amount_redeem": None,
                     "amount_outstanding": None})
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 17)
    return rows


def test_omitted_coupons_are_added_and_the_yield_prices_exactly():
    sched = schedule.build("INE0P6G07194", hella_rows(), TERMS, today=TODAY)
    added = [f for f in sched.flows if any(a["kind"] == "coupon_inferred" for a in f.adjustments)]
    assert [f.due_date for f in added] == [date(2028, 9, 17), date(2028, 12, 17), date(2029, 3, 17)]
    assert all(not f.estimated and f.event == "interest" for f in added)
    # 11% on 10,000 / 7,000 / 4,000 outstanding for the month before each repayment.
    expect = [10000 * 0.11 * daycount.year_frac(date(2028, 8, 17), date(2028, 9, 17), sched.day_count),
              7000 * 0.11 * daycount.year_frac(date(2028, 11, 17), date(2028, 12, 17), sched.day_count),
              4000 * 0.11 * daycount.year_frac(date(2029, 2, 17), date(2029, 3, 17), sched.day_count)]
    for f, e in zip(added, expect, strict=True):
        assert f.amount is not None and abs(f.amount - e) < 0.02
    assert "₹7,000 outstanding" in added[1].adjustments[0]["reason"]
    assert sched.pricing_issues(TODAY) == [] and not sched.yield_amounts_estimated(TODAY)
    v = valuation.calculate(9259.0, sched, TERMS, TODAY)
    assert v["ytm_method"] == "exact" and v["ytm"] is not None and 14.5 < v["ytm"] < 16
    assert v["ytm_reliable"] is True and v["cashflows_complete"] is True
    [note] = [w for w in v["warnings"] if w["code"] == "coupon_inferred"]
    assert note["severity"] == "info" and note["count"] == 3 and note["rate"] == "11%"
    assert note["message"].startswith("Coupons on 3 principal-repayment dates added by BondLens. NSDL lists only "
                                      "the repayment on 17 Sep 2028, 17 Dec 2028 and 17 Mar 2029; the 11% monthly coupon")
    assert all(w["severity"] == "info" for w in v["warnings"])


def test_nothing_added_when_the_schedule_already_has_the_coupons_or_is_off_cadence():
    full = schedule.build("X", hella_rows(drop_coupons_on_repayment=False), TERMS, today=TODAY)
    assert not any(a["kind"] == "coupon_inferred" for f in full.flows for a in f.adjustments)
    # Final repayment 10 days early: not on the cadence -> left alone, gap still reported.
    off = schedule.build("X", hella_rows(shift_final=-10), TERMS, today=TODAY)
    added = [f for f in off.flows if any(a["kind"] == "coupon_inferred" for a in f.adjustments)]
    assert [f.due_date for f in added] == [date(2028, 9, 17), date(2028, 12, 17)]
    # No coupon rate in the terms: nothing to infer from.
    bare = schedule.build("X", hella_rows(), {**TERMS, "coupon_rate": None}, today=TODAY)
    assert not any(a["kind"] == "coupon_inferred" for f in bare.flows for a in f.adjustments)
