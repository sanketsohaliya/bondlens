"""Schedule engine tests — one fixture per shape found in the database."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal

import pytest

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


def test_classify_frequency_real_db_values():
    # Real free-text values from bond_terms.interest_payment_frequency.
    cases = [
        ("Once a year", "annual", 1),
        ("Twelve times a year", "monthly", 12),
        ("QUARTERLY", "quarterly", 4),
        ("Twice a year", "semiannual", 2),
        ("On Maturity", "cumulative", None),
        ("CUMULATIVE", "cumulative", None),
        ("cumulative along with redemption/buyback", "cumulative", None),
        ("Four times a year", "quarterly", 4),
        ("Irregular Occurence", "irregular", None),
        # 'SEMI ANNUAL' must not fall through to the 'annual' needle.
        ("SEMI ANNUAL ON 31 MAR AND 30 SEP", "semiannual", 2),
        ("HALF YEARLY", "semiannual", 2),
        ("PAYABLE ANNUALLY", "annual", 1),
        ("ANNUAL ON 25 APR", "annual", 1),
        ("1ST APRIL EVERY YEAR", "annual", 1),
        ("PAYABLE ON MONTHLY BASIS ON 01/12/2021,01/01/2022 AND SO ON "
         "TILL MATURITY.", "monthly", 12),
        ("QUARTERLY 31ST AUG, 30TH NOV, 28TH FEB AND 31ST MAY EVERY YEAR",
         "quarterly", 4),
        (None, None, None),
        ("", None, None),
    ]
    for text, name, ppy in cases:
        assert schedule.classify_frequency(text) == (name, ppy), text


def test_rate_filed_as_amount_repaired():
    # INE0IID07751 shape: 12.75% MONTHLY bond on face 1000 where NSDL filed
    # the coupon rate (12.75) into every amount field, redemption included.
    flows = [_cf("Interest", date(2026, m, 28), payable=12.75)
             for m in range(1, 13)]
    flows.append(_cf("Full Redemption", date(2029, 12, 29), payable=12.75))
    s = schedule.build("X", flows, _terms(
        coupon_rate=12.75, interest_payment_frequency="Twelve times a year",
        redemption_date=date(2029, 12, 29), allotment_date=date(2025, 12, 28)), today=date(2025, 12, 1))
    interest = [f for f in s.flows if f.event == "interest"]
    previous = date(2025, 12, 28)
    for f in interest:
        assert f.amount == round(1000 * 12.75 / 100 * (f.due_date - previous).days / 365, 2)
        previous = f.due_date
    assert all(f.estimated for f in interest)
    redemption = [f for f in s.flows if f.event == "redemption"][0]
    assert redemption.amount == 1000  # the earlier implausible-vs-face repair
    assert s.amounts_estimated
    assert any("coupon rate" in w for w in s.warnings)


def test_amount_equal_to_rate_legitimately_untouched():
    # Face 100, annual, 12.75%: amount == rate is CORRECT (100 x 12.75% = 12.75).
    flows = [_cf("Interest", date(2027 + i, 1, 1), payable=12.75)
             for i in range(3)]
    flows.append(_cf("Full Redemption", date(2030, 1, 1), redeem=100))
    s = schedule.build("X", flows, _terms(face_value=100, coupon_rate=12.75),
                       today=TODAY)
    interest = [f for f in s.flows if f.event == "interest"]
    assert all(f.amount == 12.75 and not f.estimated for f in interest)
    assert not s.warnings


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


# --- money-timing: payment date governs future/receivable ------------------

def test_paid_early_flow_not_receivable_by_buyer():
    # Due after today but PAID before it (business-day adjustment): the
    # money is gone — a buyer settling today doesn't receive it, and it
    # must not appear in future flows or the YTM stream.
    f = schedule.Flow(event="interest", due_date=TODAY + timedelta(days=2),
                      amount=50.0, payment_date=TODAY - timedelta(days=1))
    assert not f.receivable_by_buyer(TODAY)
    assert f.effective_date == TODAY - timedelta(days=1)
    upcoming = schedule.Flow(event="interest",
                             due_date=TODAY + timedelta(days=2), amount=50.0)
    assert upcoming.receivable_by_buyer(TODAY)


def test_future_flows_use_payment_date():
    flows = [_cf("Interest", date(2027, 1, 1), payable=100),
             _cf("Full Redemption", date(2030, 1, 1), redeem=1000)]
    s = schedule.build("X", flows, _terms(), today=TODAY)
    # Manually mark the 2027 coupon as paid early, before "today".
    for f in s.flows:
        if f.event == "interest":
            f.payment_date = TODAY - timedelta(days=1)
    assert all(f.event == "redemption" for f in s.future_flows(TODAY))


# --- NSDL tranche duplication (INE523L07728 / INE523L07751 regression) -----

def test_nsdl_duplicated_schedule_collapsed():
    # 955NWIL27 shape: NSDL repeated the whole per-unit schedule 15-27x
    # (once per further-issue tranche). Without dedup this classified as
    # staggered and rescaled amounts "issue-level" by 15 -> 16.94% YTM.
    flows = []
    for year in range(2023, 2028):
        flows += [_cf("Interest", date(year, 7, 29), payable=95.5)] * 27
    flows += [_cf("Redemption", date(2027, 7, 29), payable=1000)] * 15
    s = schedule.build("X", flows, _terms(
        coupon_rate=9.55, redemption_date=date(2027, 7, 15)), today=TODAY)
    assert s.shape == "plain"
    assert s.scale == 1.0
    assert s.usable and s.redemption_matches_face
    assert len(s.flows) == 6  # 5 coupons + 1 redemption
    [red] = [f for f in s.flows if f.event == "redemption"]
    assert red.amount == 1000
    assert any("duplicated schedule rows" in w for w in s.warnings)


def test_nsdl_duplicated_cumulative_collapsed():
    # NWIL27 shape: zero-coupon-style payout duplicated 18x/10x -> was
    # showing 41% YTM from 10 "partial" redemptions + 18 interest flows.
    flows = ([_cf("Interest", date(2027, 7, 29), payable=578.25)] * 18
             + [_cf("Redemption", date(2027, 7, 29), payable=1000)] * 10)
    s = schedule.build("X", flows, _terms(
        coupon_rate=0, interest_payment_frequency=None,
        redemption_date=date(2027, 7, 15)), today=TODAY)
    assert s.shape in ("zero_coupon", "cumulative")
    assert s.scale == 1.0
    assert s.usable
    assert len(s.flows) == 2
    assert sum(f.amount or 0 for f in s.flows) == 1578.25


# --- above-face redemption payouts split into principal + interest ---------

def test_cumulative_payout_split_into_principal_and_interest():
    # CYQURE INE0Z4807015: face 1,00,000 zero coupon publishing the whole
    # maturity payout (1,60,597) as one Full Redemption row. The excess is
    # interest — otherwise projected principal exceeds what was lent.
    flows = [_cf("Full Redemption", date(2028, 3, 17), payable=160597)]
    s = schedule.build("X", flows, _terms(
        face_value=100000, coupon_rate=0, coupon_basis="Zero Coupon",
        interest_payment_frequency="On Maturity",
        redemption_date=date(2028, 3, 17)), today=TODAY)
    assert s.usable and s.redemption_matches_face
    [red] = [f for f in s.flows if f.event == "redemption"]
    [inte] = [f for f in s.flows if f.event == "interest"]
    assert red.amount == 100000
    assert inte.amount == 60597
    assert inte.due_date == red.due_date
    assert any("reclassified as interest" in w for w in s.warnings)


def test_double_face_payout_split():
    # CHEMMANUR INE051307CC2 shape: face 1,000, published redemption
    # 2,000.28 with no interest rows.
    flows = [_cf("Full Redemption", date(2031, 9, 10), payable=2000.28)]
    s = schedule.build("X", flows, _terms(
        face_value=1000, coupon_rate=None,
        interest_payment_frequency="On Maturity",
        redemption_date=date(2031, 9, 10)), today=TODAY)
    [red] = [f for f in s.flows if f.event == "redemption"]
    [inte] = [f for f in s.flows if f.event == "interest"]
    assert red.amount == 1000
    assert inte.amount == 1000.28


def test_exact_face_redemption_not_split():
    flows = [_cf("Interest", date(2027, 1, 1), payable=100),
             _cf("Full Redemption", date(2030, 1, 1), redeem=1000)]
    s = schedule.build("X", flows, _terms(), today=TODAY)
    assert len([f for f in s.flows if f.event == "interest"]) == 1
    assert not any("reclassified" in w for w in s.warnings)


def test_premium_within_tolerance_not_split():
    # 1.5% premium sits inside _FACE_TOL — leave the row alone.
    flows = [_cf("Full Redemption", date(2030, 1, 1), redeem=1015)]
    s = schedule.build("X", flows, _terms(coupon_rate=None), today=TODAY)
    [red] = [f for f in s.flows if f.event == "redemption"]
    assert red.amount == 1015
    assert not any("reclassified" in w for w in s.warnings)


# --- YTM audit regressions (2026-08) ----------------------------------------

def test_final_coupon_folded_and_listed_drops_copy():
    # 975AHFL28 / INE538L07536: NSDL lists the final coupon BOTH as its
    # own row (97.50) AND inside the redemption (1,097.50) — the folded
    # copy double-counted the coupon and inflated YTM to 13.84% (true
    # ~9.5%, OTC prints 8.39%).
    flows = [_cf("Interest", date(2026, 9, 29), payable=97.5),
             _cf("Interest", date(2027, 9, 29), payable=97.5),
             _cf("Interest", date(2028, 9, 29), payable=97.5),
             _cf("Full Redemption", date(2028, 9, 29), payable=1097.5)]
    s = schedule.build("X", flows, _terms(
        coupon_rate=9.75, redemption_date=date(2028, 9, 29)), today=TODAY)
    assert s.usable
    [red] = [f for f in s.flows if f.event == "redemption"]
    assert red.amount == 1000
    final = [f for f in s.flows
             if f.event == "interest" and f.due_date == date(2028, 9, 29)]
    assert len(final) == 1 and final[0].amount == 97.5  # no duplicate added
    assert any("folded copy dropped" in w for w in s.warnings)
    from backend.analytics import yields
    y = yields.exact_ytm(1090.0, s, today=date(2026, 8, 20))
    assert y is not None and 9.0 < y < 10.0  # was 13.84


def test_cumulative_split_still_works_when_no_same_date_interest():
    # CYQURE-style: no separate final interest row — the excess IS new
    # information and must still be split out (regression guard for the
    # duplicate-drop change).
    flows = [_cf("Full Redemption", date(2028, 3, 17), payable=160597)]
    s = schedule.build("X", flows, _terms(
        face_value=100000, coupon_rate=0, coupon_basis="Zero Coupon",
        interest_payment_frequency="On Maturity",
        redemption_date=date(2028, 3, 17)), today=TODAY)
    [inte] = [f for f in s.flows if f.event == "interest"]
    assert inte.amount == 60597


def test_interest_on_unverified_face_basis_is_withheld():
    # 79NHIT35 / INE0H7R07017: current face 300, coupon 7.9%, but NSDL
    # publishes interest on the 3,000 original-face basis (~119/half) —
    # the old repair divided by an implied factor to lower the yield.
    # That factor alone cannot establish the correct cashflow units.
    flows = [_cf("Interest", date(2026, 11, 14), payable=119.47),
             _cf("Interest", date(2027, 5, 14), payable=117.53),
             _cf("Interest", date(2027, 11, 14), payable=119.47),
             _cf("Partial Redemption", date(2027, 11, 14), redeem=150),
             _cf("Partial Redemption", date(2035, 10, 25), redeem=150)]
    s = schedule.build("X", flows, _terms(
        face_value=300, coupon_rate=7.9,
        interest_payment_frequency="Twice a year",
        redemption_type="Partial Redemption By Face Value",
        redemption_date=date(2035, 10, 25)), today=TODAY)
    assert s.usable and not s.amounts_estimated
    assert any("original face basis" in w for w in s.pricing_issues(TODAY))
    amounts = [f.amount for f in s.flows if f.event == "interest"]
    # An inferred factor of ~10 is not independent evidence for rescaling.
    assert amounts == [119.47, 117.53, 119.47]


def test_sane_interest_amounts_not_rescaled():
    # 9.55% annual on face 1000 — implied rate matches the coupon.
    flows = [_cf("Interest", date(2027 + i, 1, 1), payable=95.5) for i in range(3)]
    flows.append(_cf("Full Redemption", date(2030, 1, 1), redeem=1000))
    s = schedule.build("X", flows, _terms(coupon_rate=9.55), today=TODAY)
    assert s.usable
    assert not any("face basis" in w for w in s.warnings)


def test_missing_future_coupons_synthesized():
    # 738REC27TF / INE020B07GX4: only one long-past interest row; the
    # synthesized redemption alone priced a couponless bond → -5.09% on
    # a premium tax-free (true ~5.2%). Coupons are now synthesized from
    # terms and the exact solve lands near the OTC print.
    flows = [_cf("Interest", date(2013, 12, 1), payable=73.8)]
    s = schedule.build("X", flows, _terms(
        coupon_rate=7.38, redemption_date=date(2027, 12, 19)), today=TODAY)
    assert s.usable and s.amounts_estimated
    assert any("synthesized from terms" in w for w in s.warnings)
    future_int = [f for f in s.flows
                  if f.event == "interest" and f.due_date > TODAY]
    assert len(future_int) == 3  # Dec 2026 + Dec 2027 plus the final maturity stub
    assert [f.due_date for f in future_int] == [date(2026, 12, 1), date(2027, 12, 1), date(2027, 12, 19)]
    assert [f.amount for f in future_int] == [73.8, 73.8, 3.64]
    assert all(f.estimated for f in future_int)
    from backend.analytics import yields
    y = yields.exact_ytm(1072.0, s, today=date(2026, 8, 20))
    assert y is not None and 4.5 < y < 6.0  # was -5.09


def test_misleading_on_maturity_frequency_also_synthesized():
    # The frequency free-text ends "…AND ON MATURITY" → classified
    # 'cumulative', but the redemption is exactly face so nothing carries
    # the coupons: synthesize (annual fallback), don't trust the label.
    flows = [_cf("Interest", date(2013, 12, 1), payable=73.8)]
    s = schedule.build("X", flows, _terms(
        coupon_rate=7.38,
        interest_payment_frequency=(
            "1ST INTEREST PAYMENT WILL BE ON 01/12/2013 AND THEREAFTER IN "
            "1ST DECEMBER EACH YEAR AND ON MATURITY"),
        redemption_date=date(2027, 12, 19)), today=TODAY)
    assert s.usable
    assert any("synthesized from terms" in w for w in s.warnings)


def test_zero_coupon_without_interest_rows_still_usable():
    flows = [_cf("Full Redemption", date(2028, 1, 1), redeem=1000)]
    s = schedule.build("X", flows, _terms(
        coupon_rate=None, coupon_basis="Zero Coupon",
        interest_payment_frequency=None), today=TODAY)
    assert s.usable


def test_genuine_staggered_untouched_by_dedup():
    # Real partial redemptions differ in date (or amount) — the dedup
    # must not collapse them.
    flows = [_cf("Interest", date(2027, 1, 1), payable=100),
             _cf("Partial Redemption", date(2027, 1, 1), redeem=500),
             _cf("Interest", date(2028, 1, 1), payable=50),
             _cf("Partial Redemption", date(2028, 1, 1), redeem=500)]
    s = schedule.build("X", flows, _terms(
        redemption_type="Partial Redemption By Face Value"), today=TODAY)
    assert s.shape == "staggered"
    assert s.usable
    assert len([f for f in s.flows if f.event == "redemption"]) == 2
    assert not any("duplicated" in w for w in s.warnings)


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


def test_accreted_interest_row_at_maturity_is_zero_coupon():
    # INE0BUS07CA1 shape: 'Zero Interest' bond where NSDL files the accreted
    # interest as a separate Interest row on the redemption date. Used to
    # misclassify as 'plain' (interest + redemption present).
    flows = [_cf("Full Redemption", date(2031, 10, 30), payable=1000),
             _cf("Interest", date(2031, 10, 30), payable=1000.83)]
    s = schedule.build("X", flows, _terms(
        coupon_rate=None, coupon_basis="Zero Interest",
        interest_payment_frequency=None), today=TODAY)
    assert s.shape == "zero_coupon"
    assert s.frequency == "cumulative"
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


def test_unverified_small_redemption_is_preserved_and_withheld():
    flows = [_cf("Interest", date(2027, 1, 1), payable=100),
             _cf("Full Redemption", date(2030, 1, 1), redeem=59)]
    s = schedule.build("X", flows, _terms(), today=TODAY)
    assert not s.usable and s.pricing_issues(TODAY)
    red = [f for f in s.flows if f.event == "redemption"][0]
    assert red.amount == 59 and not red.estimated


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


def test_missing_coupon_periods_block_pricing_without_inventing_flows():
    from backend.analytics import yields

    flows = [_cf("Interest", date(2027, 1, 1), payable=100),
             _cf("Full Redemption", date(2030, 1, 1), redeem=1000)]
    s = schedule.build("X", flows, _terms(), today=TODAY)
    assert s.pricing_issues(TODAY)
    assert yields.exact_ytm(1000, s, TODAY) is None
    assert len(s.flows) == 2


def test_final_coupon_gap_is_not_hidden_by_principal_repayment():
    s = schedule.build("X", [_cf("Interest", date(2027, 1, 1), payable=100),
                             _cf("Redemption", date(2028, 1, 1), redeem=1000)],
                       _terms(redemption_date=date(2028, 1, 1)), today=TODAY)
    assert s.pricing_issues(TODAY)


def test_combined_interest_and_principal_payments_are_not_missing_coupons():
    flows = [_cf("Interest", date(2027, 1, 1), payable=100),
             _cf("Redemption", date(2028, 1, 1), payable=600),
             _cf("Redemption", date(2029, 1, 1), payable=550)]
    s = schedule.build("X", flows, _terms(redemption_date=date(2029, 1, 1)), today=TODAY)
    assert not s.pricing_issues(TODAY)
    assert sum(f.amount or 0 for f in s.flows) == 1250


def test_monthly_coupons_clustered_on_consecutive_days_require_review():
    flows = [_cf("Interest", date(2027, 1, d), payable=8.5) for d in range(1, 10)]
    flows.append(_cf("Redemption", date(2027, 9, 1), redeem=1000))
    s = schedule.build("X", flows, _terms(interest_payment_frequency="Monthly",
                                          redemption_date=date(2027, 9, 1)), today=TODAY)
    assert s.pricing_issues(TODAY)


def test_conflicting_payment_dates_are_not_resolved_by_row_order():
    one = {**_cf("Interest", date(2027, 1, 1), payable=100), "payment_date": date(2027, 1, 1)}
    two = {**one, "payment_date": date(2027, 1, 4)}
    flows = [one, two, _cf("Redemption", date(2027, 1, 1), redeem=1000)]
    for rows in (flows, flows[::-1]):
        s = schedule.build("X", rows, _terms(redemption_date=date(2027, 1, 1)), today=TODAY)
        assert any("conflicting" in reason.lower() for reason in s.pricing_issues(TODAY))


def test_split_cumulative_payment_preserves_its_payment_date():
    payment = date(2027, 1, 4)
    flows = [{**_cf("Redemption", date(2027, 1, 1), redeem=1600), "payment_date": payment}]
    s = schedule.build("X", flows, _terms(coupon_rate=None,
                                          redemption_date=date(2027, 1, 1)), today=TODAY)
    assert all(f.payment_date == payment for f in s.flows)


def test_yield_validation_is_scoped_to_the_call_date():
    flows = [_cf("Interest", date(2027, 1, 1), payable=100),
             _cf("Interest", date(2028, 1, 1)),
             _cf("Redemption", date(2029, 1, 1), redeem=1000)]
    s = schedule.build("X", flows, _terms(coupon_rate=None,
                                          redemption_date=date(2029, 1, 1)), today=TODAY)
    assert s.pricing_issues(TODAY)
    assert not s.pricing_issues(TODAY, until=date(2027, 1, 1))


def test_hyphenated_half_yearly_frequency():
    assert schedule.classify_frequency("Half-yearly") == ("semiannual", 2)


@pytest.mark.parametrize("face,coupon,previous,maturity,total,frequency", [
    (100000, 10.5, date(2027, 7, 28), date(2027, 8, 27), 100863.01, "Monthly"),
    (10000, 11, date(2027, 7, 5), date(2027, 8, 5), 10093.42, "Monthly"),
    (1000, 7.53, date(2032, 8, 1), date(2033, 8, 1), 1075.3, "Annual"),
])
def test_terminal_interest_includes_principal_without_adding_money(face, coupon, previous, maturity, total, frequency):
    from backend.analytics import yields

    record, payment = maturity - timedelta(days=7), maturity + timedelta(days=2)
    rows = [_cf("Interest", previous, payable=face * coupon / 100 / (12 if frequency == "Monthly" else 1)),
            {**_cf("Interest", maturity, payable=total, record=record), "payment_date": payment}]
    before = deepcopy(rows)
    s = schedule.build("X", rows, _terms(face_value=face, coupon_rate=coupon,
                                          redemption_date=maturity, interest_payment_frequency=frequency),
                       today=previous - timedelta(days=1))
    principal = [f for f in s.flows if f.event == "redemption"]
    assert len(principal) == 1 and principal[0].amount == face
    assert principal[0].record_date == record and principal[0].payment_date == payment
    assert sum(f.amount or 0 for f in s.flows) == pytest.approx(sum(r["amount_payable"] for r in rows))
    assert not s.amounts_estimated
    assert principal[0].adjustments[0]["kind"] == "combined_payment_split"
    assert principal[0].adjustments[0]["previous_amount"] == total
    assert yields.exact_ytm(face, s, previous - timedelta(days=1)) is not None
    assert rows == before


@pytest.mark.parametrize("offset,total", [(6, 100883.29), (0, 200000)])
def test_ambiguous_terminal_lump_does_not_get_an_extra_principal_or_fallback(offset, total):
    from backend.analytics import compute, yields

    maturity = date(2027, 8, 27)
    terms = _terms(face_value=100000, coupon_rate=10.5, redemption_date=maturity,
                   interest_payment_frequency="Monthly")
    rows = [_cf("Interest", date(2027, 7, 27), payable=891.78),
            _cf("Interest", maturity - timedelta(days=offset), payable=total)]
    s = schedule.build("X", rows, terms, today=date(2027, 7, 1))
    assert not any(f.event == "redemption" and f.estimated for f in s.flows)
    assert s.pricing_issues(date(2027, 7, 1))
    assert yields.exact_ytm(100000, s, date(2027, 7, 1)) is None
    assert yields.fallback_ytm(100000, s, terms, date(2027, 7, 1)) is None
    assert compute.payout_summary("X", rows, terms, date(2027, 7, 1))["payout_total_snapshot"] is None


def test_synthesized_staggered_coupons_follow_outstanding_principal():
    from backend.analytics import yields

    start, middle, end = date(2025, 1, 1), date(2026, 1, 1), date(2027, 1, 1)
    rows = [_cf("Partial Redemption", middle, redeem=500), _cf("Partial Redemption", end, redeem=500)]
    s = schedule.build("X", rows, _terms(allotment_date=start, redemption_date=end,
                                          day_count_convention="Actual/365"), today=start)
    assert [(f.due_date, f.amount) for f in s.flows if f.event == "interest"] == [(middle, 100), (end, 50)]
    assert yields.exact_ytm(1000, s, start) == 10.0
    assert all(f.adjustments for f in s.flows if f.estimated)


def test_missing_coupon_uses_current_face_without_subtracting_history_twice():
    start, end = date(2026, 1, 1), date(2027, 1, 1)
    rows = [_cf("Partial Redemption", date(2025, 1, 1), redeem=200),
            _cf("Interest", start, payable=80), _cf("Interest", end),
            _cf("Partial Redemption", end, redeem=800)]
    s = schedule.build("X", rows, _terms(face_value=800, redemption_date=end,
                                          day_count_convention="Actual/365"), today=start)
    [coupon] = [f for f in s.flows if f.event == "interest" and f.due_date == end]
    assert coupon.amount == 80 and coupon.estimated
    assert coupon.adjustments[0]["kind"] == "coupon_estimated"
    assert coupon.adjustments[0]["previous_amount"] is None


def test_missing_coupon_integrates_mid_period_principal_repayments():
    start, partial, end = date(2025, 1, 1), date(2025, 7, 2), date(2026, 1, 1)
    rows = [_cf("Interest", start, payable=100), _cf("Partial Redemption", partial, redeem=500),
            _cf("Interest", end), _cf("Partial Redemption", end, redeem=500)]
    s = schedule.build("X", rows, _terms(redemption_date=end, day_count_convention="Actual/365"), today=start)
    [coupon] = [f for f in s.flows if f.event == "interest" and f.estimated]
    assert coupon.amount == 74.93


def test_missing_first_coupon_uses_allotment_anchor():
    start, end = date(2025, 1, 1), date(2026, 1, 1)
    s = schedule.build("X", [_cf("Interest", end), _cf("Redemption", end, redeem=1000)],
                       _terms(allotment_date=start, redemption_date=end, day_count_convention="Actual/365"), today=start)
    [coupon] = [f for f in s.flows if f.event == "interest"]
    assert coupon.amount == 100 and coupon.estimated


def test_synthesis_without_a_coupon_anchor_is_withheld():
    from backend.analytics import yields

    end = date(2027, 1, 1)
    terms = _terms(redemption_date=end)
    s = schedule.build("X", [_cf("Redemption", end, redeem=1000)], terms, today=TODAY)
    assert not any(f.event == "interest" for f in s.flows)
    assert yields.exact_ytm(1000, s, TODAY) is None
    assert yields.fallback_ytm(1000, s, terms, TODAY) is None


def test_synthesized_month_end_dates_do_not_drift():
    s = schedule.build("X", [_cf("Redemption", date(2025, 5, 31), redeem=1000)],
                       _terms(allotment_date=date(2025, 1, 31), redemption_date=date(2025, 5, 31),
                              interest_payment_frequency="Monthly"), today=date(2025, 1, 31))
    assert [f.due_date for f in s.flows if f.event == "interest"] == [
        date(2025, 2, 28), date(2025, 3, 31), date(2025, 4, 30), date(2025, 5, 31)]


def test_issue_level_scaling_requires_independent_quantity_evidence():
    from backend.analytics import yields

    rows = [_cf("Interest", date(2027, 1, 1), payable=1000),
            _cf("Redemption", date(2028, 1, 1), redeem=10000)]
    s = schedule.build("X", rows, _terms(redemption_date=date(2028, 1, 1)), today=TODAY)
    assert s.scale == 1
    assert s.pricing_issues(TODAY)
    assert yields.exact_ytm(1000, s, TODAY) is None


@pytest.mark.parametrize("overrides", [
    {"coupon_basis": "Variable Interest"},
    {"coupon_type": "Floating"},
    {"coupon_basis": "Zero Interest"},
    {"interest_payment_frequency": "On Maturity"},
])
def test_unknown_coupons_are_not_filled_from_unsupported_headline_rates(overrides):
    from backend.analytics import yields

    end = date(2027, 1, 1)
    rows = [_cf("Interest", date(2026, 1, 1), payable=100), _cf("Interest", end),
            _cf("Redemption", end, redeem=1000)]
    s = schedule.build("X", rows, _terms(redemption_date=end, **overrides), today=TODAY)
    future = [f for f in s.flows if f.event == "interest" and f.due_date == end]
    assert future[0].amount is None
    assert yields.exact_ytm(1000, s, TODAY) is None


def test_rate_repair_also_uses_the_staggered_principal_timeline():
    start, middle, end = date(2025, 1, 1), date(2026, 1, 1), date(2027, 1, 1)
    rows = [_cf("Interest", middle, payable=10), _cf("Redemption", middle, redeem=500),
            _cf("Interest", end, payable=10), _cf("Redemption", end, redeem=500)]
    s = schedule.build("X", rows, _terms(allotment_date=start, redemption_date=end,
                                          day_count_convention="Actual/365"), today=start)
    assert [f.amount for f in s.flows if f.event == "interest"] == [100, 50]
    assert all(f.adjustments[0]["kind"] == "coupon_rate_repaired" for f in s.flows if f.event == "interest")


@pytest.mark.parametrize("premium,last_coupon,placeholder", [("2% premium", 100, True), (None, 1100, True), ("2% premium", 100, False)])
def test_ambiguous_missing_principal_is_not_assumed_to_be_par(premium, last_coupon, placeholder):
    from backend.analytics import yields

    start, end = date(2025, 1, 1), date(2026, 1, 1)
    rows = [_cf("Interest", start, payable=100), _cf("Interest", end, payable=last_coupon)]
    if placeholder:
        rows.append(_cf("Redemption", end))
    s = schedule.build("X", rows, _terms(redemption_date=end, redemption_premium_details=premium), today=start)
    assert [f.amount for f in s.flows if f.event == "redemption"] == ([None] if placeholder else [])
    assert yields.exact_ytm(1100, s, start) is None


@pytest.mark.parametrize("premium,redeem_field", [("5% final premium", True), (None, True), (None, False)])
def test_redemption_excess_does_not_cover_missing_coupons(premium, redeem_field):
    from backend.analytics import compute, yields

    start = date(2025, 1, 1)
    terms = _terms(redemption_date=date(2028, 1, 1), day_count_convention="Actual/365",
                   redemption_type="Partial Redemption By Face Value", redemption_premium_details=premium)
    rows = [_cf("Interest", date(2026, 1, 1), payable=100),
            _cf("Partial Redemption", date(2027, 1, 1), **({"redeem": 500} if redeem_field else {"payable": 500})),
            _cf("Full Redemption", date(2028, 1, 1), **({"redeem": 550} if redeem_field else {"payable": 550}))]
    s = schedule.build("X", rows, terms, today=start)
    assert s.pricing_issues(start)
    assert yields.exact_ytm(1000, s, start) is None
    assert yields.fallback_ytm(1000, s, terms, start) is None
    assert compute.payout_summary("X", rows, terms, start)["payout_total_snapshot"] is None
    complete = schedule.build("X", rows + [_cf("Interest", date(2027, 1, 1), payable=100),
                                           _cf("Interest", date(2028, 1, 1), payable=50)], terms, today=start)
    assert yields.exact_ytm(1000, complete, start) == 11.74


def test_verified_combined_installments_preserve_cash_and_reconcile_components():
    from backend.analytics import yields

    start = date(2025, 1, 1)
    rows = [_cf("Interest", date(2026, 1, 1), payable=100),
            _cf("Redemption", date(2027, 1, 1), payable=600),
            _cf("Redemption", date(2028, 1, 1), payable=550)]
    before = deepcopy(rows)
    terms = _terms(redemption_date=date(2028, 1, 1), day_count_convention="Actual/365",
                   redemption_type="Partial Redemption By Face Value", redemption_premium_details="No")
    s = schedule.build("X", rows, terms, today=start)
    assert not s.pricing_issues(start)
    assert sum(f.amount or 0 for f in s.flows if f.event == "redemption") == 1000
    assert sum(f.amount or 0 for f in s.flows if f.event == "interest") == 250
    assert yields.exact_ytm(1000, s, start) == 10
    assert s.amounts_estimated
    assert rows == before


def test_coupon_labelled_redemption_is_reclassified_only_when_principal_reconciles():
    from backend.analytics import yields

    rows = [_cf("Interest", date(2026, 1, 1), payable=100),
            _cf("Redemption", date(2027, 1, 1), payable=100),
            _cf("Interest", date(2028, 1, 1), payable=100),
            _cf("Redemption", date(2029, 1, 1), payable=1100)]
    s = schedule.build("X", rows, _terms(redemption_date=date(2029, 1, 1),
                                          day_count_convention="Actual/Actual"), today=date(2025, 1, 1))
    assert len([f for f in s.flows if f.event == "redemption"]) == 1
    assert sum(f.amount or 0 for f in s.flows) == 1400
    assert yields.exact_ytm(1000, s, date(2025, 1, 1)) == 10


def test_combined_installments_preserve_source_precision():
    rows = [_cf("Interest", date(2026, 1, 1), payable=100),
            _cf("Redemption", date(2027, 1, 1), payable=600.004),
            _cf("Redemption", date(2028, 1, 1), payable=550.002)]
    s = schedule.build("X", rows, _terms(redemption_date=date(2028, 1, 1),
                                          day_count_convention="Actual/365"), today=date(2025, 1, 1))
    assert sum(f.amount or 0 for f in s.flows) == pytest.approx(1250.006, abs=1e-10)


def test_explicit_premium_cannot_be_reclassified_as_installment_coupons():
    from backend.analytics import yields

    rows = [_cf("Interest", date(2026, 1, 1), payable=100),
            _cf("Redemption", date(2027, 1, 1), payable=600),
            _cf("Redemption", date(2028, 1, 1), payable=550)]
    s = schedule.build("X", rows, _terms(redemption_date=date(2028, 1, 1),
                                          redemption_premium_details="Premium payable with installments"),
                       today=date(2025, 1, 1))
    assert yields.exact_ytm(1000, s, date(2025, 1, 1)) is None


@pytest.mark.parametrize("event", ["Interest", "Full Redemption"])
def test_terminal_premium_is_not_evidence_of_a_coupon(event):
    from backend.analytics import yields

    start, end = date(2025, 1, 1), date(2026, 1, 1)
    rows = [_cf("Interest", start, payable=100), _cf(event, end, payable=1100)]
    s = schedule.build("X", rows, _terms(redemption_date=end,
                                          redemption_premium_details="10% premium"), today=start)
    assert yields.exact_ytm(1000, s, start) is None


def test_declared_premium_matching_coupon_is_not_dropped_as_duplicate():
    from backend.analytics import yields

    start, end = date(2025, 1, 1), date(2026, 1, 1)
    rows = [_cf("Interest", end, payable=100), _cf("Full Redemption", end, payable=1100)]
    s = schedule.build("X", rows, _terms(redemption_date=end, day_count_convention="Actual/365",
                                          redemption_premium_details="10% premium"), today=start)
    assert sum(f.amount or 0 for f in s.flows) == 1200
    assert yields.exact_ytm(1000, s, start) == 20


def test_cumulative_buyback_options_are_not_mandatory_cashflows():
    from backend.analytics import yields

    rows = []
    for year, amount in [(2018, 3995), (2021, 6569), (2023, 8682), (2026, 12596)]:
        rows.extend([_cf("Interest", date(year, 12, 12), payable=amount),
                     _cf("Full Redemption", date(year, 12, 12), payable=5000)])
    s = schedule.build("X", rows, _terms(face_value=5000, coupon_rate=8.75,
                                          allotment_date=date(2011, 12, 12), redemption_date=date(2026, 12, 12),
                                          redemption_type="Partial Redemption By Quantity",
                                          interest_payment_frequency="cumulative along with redemption/buyback"),
                       today=date(2016, 1, 1))
    assert len(s.flows) == 8
    assert sum(f.optional for f in s.flows) == 6
    assert sum(f.amount or 0 for f in s.future_flows(date(2016, 1, 1))) == 17596
    assert s.principal_at(date(2026, 12, 12), date(2026, 9, 11)) == 5000
    assert yields.exact_ytm(16840, s, date(2026, 9, 11)) == 19.03
    before = deepcopy(s.flows)
    assert yields.yield_to_call(5000, s, [date(2018, 12, 12)], date(2011, 12, 12)) == pytest.approx(8.75, abs=0.01)
    assert s.flows == before


def test_partial_face_repayments_are_not_optional_exits():
    rows = [_cf("Partial Redemption", date(2025, 1, 1), redeem=500),
            _cf("Interest", date(2027, 1, 1), payable=50),
            _cf("Partial Redemption", date(2027, 1, 1), redeem=500)]
    s = schedule.build("X", rows, _terms(face_value=500, redemption_date=date(2027, 1, 1),
                                          redemption_type="Partial Redemption By Face Value"), today=TODAY)
    assert not any(f.optional for f in s.flows)
