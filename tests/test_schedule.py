"""Schedule engine tests — one fixture per shape found in the database."""
from datetime import date
from decimal import Decimal

from backend.analytics import schedule

TODAY = date(2026, 7, 12)


def _terms(**kw):
    base = {
        "face_value": 1000, "coupon_rate": 10, "perpetual": False,
        "day_count_convention": "Actual/actual",
        "interest_payment_frequency": "Once a year",
        "redemption_type": "Full Redemption", "redemption_date": date(2030, 1, 1),
        "coupon_basis": "Fixed Interest", "net_quantity": None,
    }
    base.update(kw)
    return base


def _cf(event, due, payable=None, redeem=None, record=None):
    return {"event": event, "due_date": due, "record_date": record,
            "amount_payable": payable, "amount_redeem": redeem,
            "amount_outstanding": None}


def test_plain_annual():
    flows = [_cf("Interest", date(2027 + i, 1, 1), payable=100) for i in range(3)]
    flows.append(_cf("Full Redemption", date(2030, 1, 1), redeem=1000))
    s = schedule.build("X", flows, _terms(), today=TODAY)
    assert s.shape == "plain"
    assert s.usable
    assert s.frequency == "annual" and s.periods_per_year == 1
    assert not s.amounts_estimated
    assert len(s.future_flows(TODAY)) == 4


def test_staggered_partial_redemption():
    flows = [_cf("Interest", date(2027, 1, 1), payable=100),
             _cf("Partial Redemption", date(2027, 1, 1), redeem=500),
             _cf("Interest", date(2028, 1, 1), payable=50),
             _cf("Partial Redemption", date(2028, 1, 1), redeem=500)]
    s = schedule.build("X", flows, _terms(
        redemption_type="Partial Redemption By Face Value"), today=TODAY)
    assert s.shape == "staggered"
    assert s.usable


def test_partially_redeemed_face_is_current():
    # bond_terms.face_value reflects the reduced face; past redemptions
    # explain the difference (total redemption = 2x current face).
    flows = [_cf("Partial Redemption", date(2025, 1, 1), redeem=500),
             _cf("Partial Redemption", date(2027, 1, 1), redeem=500)]
    s = schedule.build("X", flows, _terms(face_value=500, coupon_rate=None),
                       today=TODAY)
    assert s.usable  # future 500 == current face 500


def test_zero_coupon_premium_redemption():
    flows = [_cf("Full Redemption", date(2029, 7, 29), redeem=2000)]
    s = schedule.build("X", flows, _terms(
        coupon_rate=None, coupon_basis="Zero Interest"), today=TODAY)
    assert s.shape == "zero_coupon"
    assert s.usable  # accreted payout > face accepted


def test_cumulative_on_maturity():
    flows = [_cf("Full Redemption", date(2028, 9, 1), redeem=1778)]
    s = schedule.build("X", flows, _terms(
        coupon_rate=11, interest_payment_frequency="On Maturity"), today=TODAY)
    assert s.shape == "cumulative"
    assert s.usable


def test_issue_level_rescaled():
    # Amounts published at issue level: 100,000 units of ₹1000 face.
    flows = [_cf("Interest", date(2027, 1, 1), payable=100_000 * 100),
             _cf("Full Redemption", date(2030, 1, 1), redeem=100_000 * 1000)]
    s = schedule.build("X", flows, _terms(net_quantity=100_000), today=TODAY)
    assert s.usable
    assert s.scale == 100_000
    red = [f for f in s.flows if f.event == "redemption"][0]
    assert red.amount == 1000


def test_final_coupon_folded_into_redemption():
    flows = [_cf("Interest", date(2027, 1, 1), payable=100),
             _cf("Full Redemption", date(2030, 1, 1), redeem=1100)]
    s = schedule.build("X", flows, _terms(), today=TODAY)
    assert s.usable


def test_implausible_redemption_overridden():
    flows = [_cf("Interest", date(2027, 1, 1), payable=100),
             _cf("Full Redemption", date(2030, 1, 1), redeem=59)]
    s = schedule.build("X", flows, _terms(), today=TODAY)
    assert s.usable
    red = [f for f in s.flows if f.event == "redemption"][0]
    assert red.amount == 1000 and red.estimated


def test_synthesized_redemption():
    flows = [_cf("Interest", date(2027, 1, 1), payable=100)]
    s = schedule.build("X", flows, _terms(), today=TODAY)
    assert any(f.event == "redemption" and f.estimated for f in s.flows)
    assert s.usable


def test_missing_interest_amount_estimated():
    flows = [_cf("Interest", date(2027, 1, 1), payable=100),
             _cf("Interest", date(2028, 1, 1)),  # amount missing
             _cf("Full Redemption", date(2028, 1, 1), redeem=1000)]
    s = schedule.build("X", flows, _terms(), today=TODAY)
    est = [f for f in s.flows if f.event == "interest" and f.estimated]
    assert len(est) == 1
    assert est[0].amount is not None
    assert abs(est[0].amount - 100) < 1  # 10% of 1000 over ~1yr
    assert s.amounts_estimated


def test_perpetual():
    flows = [_cf("Interest", date(2027, 1, 1), payable=100)]
    s = schedule.build("X", flows, _terms(
        perpetual=True, redemption_date=None), today=TODAY)
    assert s.shape in ("perpetual", "irregular", "plain")
    # No synthesized redemption for perpetuals.
    assert not any(f.event == "redemption" for f in s.flows)


def test_record_date_gates_buyer_flows():
    flows = [_cf("Interest", date(2026, 7, 20), payable=100,
                 record=date(2026, 7, 5)),  # record passed -> seller's
             _cf("Full Redemption", date(2030, 1, 1), redeem=1000)]
    s = schedule.build("X", flows, _terms(), today=TODAY)
    fut = s.future_flows(TODAY)
    assert all(f.event != "interest" for f in fut)


def test_decimal_inputs():
    flows = [_cf("Interest", date(2027, 1, 1), payable=Decimal("100.5")),
             _cf("Full Redemption", date(2030, 1, 1), redeem=Decimal("1000"))]
    s = schedule.build("X", flows, _terms(face_value=Decimal("1000"),
                                          coupon_rate=Decimal("10.05")),
                       today=TODAY)
    assert s.usable
    assert isinstance(s.flows[0].amount, float)


def test_empty():
    s = schedule.build("X", [], _terms(), today=TODAY)
    assert s.shape == "empty"
    assert not s.usable


def test_frequency_inferred_from_flows():
    flows = [_cf("Interest", date(2026, m, 1), payable=8) for m in range(8, 13)]
    flows.append(_cf("Full Redemption", date(2027, 1, 1), redeem=1000))
    s = schedule.build("X", flows, _terms(interest_payment_frequency=None),
                       today=TODAY)
    assert s.frequency == "monthly" and s.periods_per_year == 12
