"""Ground-truth tests for the YTM/analytics math.

Every expected value here is hand-computable — no fixtures derived from the
code under test. Run: python -m unittest discover tests -v
"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bondlens.gsec import _clean_curve
from bondlens.nsdl import (fill_amounts, normalize_flows,
                           redemption_matches_face)
from bondlens.ytm import (accrued_interest, exact_ytm, modified_duration,
                          parse_date, ytm)

TODAY = date(2026, 7, 4)


def flow(due, amount, event="Interest", record=None):
    return {"due_date": due, "record_date": record, "event": event,
            "amount": amount}


class ExactYtm(unittest.TestCase):
    def test_zero_coupon_one_year(self):
        # Pay 900 today, get 1000 in exactly 365 days: y = 1000/900 - 1 = 11.11%
        flows = [flow("04-07-2027", 1000.0, "Full Redemption")]
        self.assertAlmostEqual(exact_ytm(900.0, flows, TODAY), 11.11, places=2)

    def test_par_bond_ytm_equals_coupon(self):
        # Price = face, annual 8% coupons on exact anniversaries: YTM = 8%
        flows = [
            flow("04-07-2027", 80.0),
            flow("04-07-2028", 80.0),
            flow("04-07-2029", 80.0),
            flow("04-07-2029", 1000.0, "Full Redemption"),
        ]
        y = exact_ytm(1000.0, flows, TODAY)
        # 365-day years vs calendar (incl. 2028 leap day) skew ~1bp
        self.assertAlmostEqual(y, 8.0, delta=0.02)

    def test_discount_bond_yields_more_than_coupon(self):
        flows = [
            flow("04-07-2027", 80.0),
            flow("04-07-2027", 1000.0, "Full Redemption"),
        ]
        # Pay 960: return = 1080/960 - 1 = 12.5% over one year
        self.assertAlmostEqual(exact_ytm(960.0, flows, TODAY), 12.5, delta=0.02)

    def test_past_record_date_excludes_coupon(self):
        # Coupon due in 8 days but record date passed: buyer never gets it.
        flows = [
            flow("12-07-2026", 94.0, record="26-06-2026"),
            flow("04-07-2027", 94.0, record="19-06-2027"),
            flow("04-07-2027", 1000.0, "Full Redemption", record="19-06-2027"),
        ]
        # Only 1094 in 1y for 1000 => 9.4%
        self.assertAlmostEqual(exact_ytm(1000.0, flows, TODAY), 9.4, delta=0.02)

    def test_implausible_solution_rejected(self):
        # Solvable at ~150%/yr, but nothing legitimate yields that — the
        # schedule data must be defective, so refuse to report it.
        flows = [flow("04-07-2027", 1000.0, "Full Redemption")]
        self.assertIsNone(exact_ytm(400.0, flows, TODAY))

    def test_impossible_price_returns_none(self):
        # Future flows total 94: no yield in range reconciles a 1000 price.
        flows = [flow("04-07-2027", 94.0)]
        self.assertIsNone(exact_ytm(1000.0, flows, TODAY))

    def test_no_future_flows_returns_none(self):
        flows = [flow("04-07-2020", 94.0)]
        self.assertIsNone(exact_ytm(1000.0, flows, TODAY))


class ApproxYtm(unittest.TestCase):
    def test_par_bond(self):
        y = ytm(1000.0, 8.0, 1000.0, "04-Jul-2029", TODAY)
        self.assertAlmostEqual(y, 8.0, delta=0.05)

    def test_zero_price_none(self):
        self.assertIsNone(ytm(0.0, 8.0, 1000.0, "04-Jul-2029", TODAY))


class Accrued(unittest.TestCase):
    def test_mid_period(self):
        # Exactly halfway between coupons: half the next coupon accrued.
        flows = [flow("04-01-2026", 50.0), flow("04-01-2027", 50.0)]
        # 04-07-2026 is 181 days after 04-01-2026 of a 365-day period
        self.assertAlmostEqual(accrued_interest(flows, TODAY),
                               50.0 * 181 / 365, places=2)

    def test_before_first_coupon_none(self):
        flows = [flow("04-01-2027", 50.0)]
        self.assertIsNone(accrued_interest(flows, TODAY))


class Duration(unittest.TestCase):
    def test_zero_coupon_duration_is_maturity_over_one_plus_y(self):
        flows = [flow("04-07-2028", 1000.0, "Full Redemption")]
        y = exact_ytm(857.34, flows, TODAY)  # ~8%
        d = modified_duration(857.34, flows, y, TODAY)
        t = (date(2028, 7, 4) - TODAY).days / 365.0
        self.assertAlmostEqual(d, t / (1 + y / 100.0), delta=0.01)


class NormalizeFlows(unittest.TestCase):
    def test_issue_level_amounts_scaled_down(self):
        # 20,000-unit issue reported at issue level (INE413U07418 case)
        flows = [
            flow("04-07-2027", 94.0 * 20000),
            flow("04-07-2027", 1000.0 * 20000, "Full Redemption"),
        ]
        out = normalize_flows(flows, 1000.0)
        self.assertEqual([f["amount"] for f in out], [94.0, 1000.0])

    def test_per_unit_amounts_untouched(self):
        flows = [
            flow("04-07-2027", 94.0),
            flow("04-07-2027", 1000.0, "Full Redemption"),
        ]
        self.assertEqual(normalize_flows(flows, 1000.0), flows)

    def test_half_amortized_bond_never_scaled(self):
        # NSE face reduced to 500 after partial redemptions; schedule spans
        # the original 1000. Ratio is exactly 2 — must NOT be halved.
        flows = [
            flow("04-07-2025", 500.0, "Part Redemption"),
            flow("04-07-2027", 500.0, "Full Redemption"),
            flow("04-07-2027", 47.0),
        ]
        self.assertEqual(normalize_flows(flows, 500.0), flows)

    def test_partial_redemptions_sum_to_face(self):
        flows = [
            flow("04-07-2027", 500.0, "Part Redemption"),
            flow("04-07-2028", 500.0, "Full Redemption"),
        ]
        self.assertEqual(normalize_flows(flows, 1000.0), flows)


class RedemptionMatchesFace(unittest.TestCase):
    def test_bullet_bond_matches(self):
        flows = [
            flow("04-07-2027", 80.0),
            flow("04-07-2027", 1000.0, "Full Redemption"),
        ]
        self.assertTrue(redemption_matches_face(flows, 1000.0, TODAY))

    def test_overstated_principal_rejected(self):
        # 79NHIT40 case: NSE quotes against residual face 300, but NSDL's
        # schedule still carries 600 of future principal — exact YTM off it
        # would be fictional (19% vs ~8% real).
        flows = [
            flow("25-10-2030", 300.0, "Part Redemption"),
            flow("25-10-2040", 300.0, "Full Redemption"),
            flow("25-10-2027", 11.85),
        ]
        self.assertFalse(redemption_matches_face(flows, 300.0, TODAY))

    def test_amortizer_with_correct_residual_accepted(self):
        # Past instalments are ignored; future ones sum to the residual face.
        flows = [
            flow("04-07-2024", 300.0, "Part Redemption"),
            flow("04-07-2030", 150.0, "Part Redemption"),
            flow("04-07-2032", 150.0, "Full Redemption"),
        ]
        self.assertTrue(redemption_matches_face(flows, 300.0, TODAY))

    def test_missing_redemption_rows_pass_through(self):
        # No principal rows at all — nothing to compare, other guards apply.
        flows = [flow("04-07-2027", 80.0)]
        self.assertTrue(redemption_matches_face(flows, 1000.0, TODAY))

    def test_no_face_value_pass_through(self):
        flows = [flow("04-07-2027", 1000.0, "Full Redemption")]
        self.assertTrue(redemption_matches_face(flows, None, TODAY))

    def test_past_record_date_principal_excluded(self):
        # Redemption due in future but record date already passed — the buyer
        # won't receive it, so it must not count toward the face check.
        flows = [
            flow("12-07-2026", 300.0, "Part Redemption", record="26-06-2026"),
            flow("04-07-2030", 300.0, "Full Redemption"),
        ]
        self.assertTrue(redemption_matches_face(flows, 300.0, TODAY))


class FillAmounts(unittest.TestCase):
    def test_blank_amounts_filled_from_coupon(self):
        # The 94SFL28 case: real dates, null amounts.
        flows = [
            flow("12-07-2026", None),
            flow("12-07-2027", None),
            flow("12-07-2028", None),
            flow("12-07-2028", None, "Full Redemption"),
        ]
        out, est = fill_amounts(flows, 9.4, 1000.0, "annual")
        self.assertTrue(est)
        self.assertEqual([f["amount"] for f in out], [94.0, 94.0, 94.0, 1000.0])

    def test_quarterly_inferred_from_date_spacing(self):
        flows = [
            flow("04-01-2027", None), flow("04-04-2027", None),
            flow("04-07-2027", None),
            flow("04-07-2027", None, "Full Redemption"),
        ]
        out, est = fill_amounts(flows, 8.0, 1000.0, None)
        self.assertTrue(est)
        self.assertEqual(out[0]["amount"], 20.0)  # 8% / 4

    def test_unknown_coupon_falls_back(self):
        flows = [flow("12-07-2027", None), flow("12-07-2027", 1000.0,
                                                "Full Redemption")]
        out, est = fill_amounts(flows, None, 1000.0, "annual")
        self.assertFalse(est)
        self.assertEqual(len(out), 1)  # only the row with a real amount

    def test_partial_redemption_without_amount_never_guessed(self):
        flows = [
            flow("12-07-2027", None, "Part Redemption"),
            flow("12-07-2028", 500.0, "Full Redemption"),
        ]
        out, est = fill_amounts(flows, 9.4, 1000.0, "annual")
        self.assertFalse(est)
        self.assertEqual(len(out), 1)

    def test_complete_schedules_untouched(self):
        flows = [flow("12-07-2027", 94.0)]
        out, est = fill_amounts(flows, 9.4, 1000.0, "annual")
        self.assertFalse(est)
        self.assertEqual(out, flows)


class CleanCurve(unittest.TestCase):
    @staticmethod
    def pt(tenor, yld, traded=1000.0):
        return {"tenor": tenor, "yield": yld, "symbol": "x",
                "traded_value": traded}

    def test_stale_outlier_dropped(self):
        pts = [self.pt(1, 5.5), self.pt(2, 5.6), self.pt(3, 5.7),
               self.pt(5, 6.9), self.pt(7, 6.0), self.pt(10, 6.2),
               self.pt(15, 6.4)]
        out = _clean_curve(pts)
        self.assertNotIn(6.9, [p["yield"] for p in out])
        self.assertEqual(len(out), 6)

    def test_untraded_points_dropped_when_enough_remain(self):
        pts = [self.pt(i, 5.5 + i * 0.1) for i in range(1, 8)]
        pts.append(self.pt(9, 7.9, traded=0))
        out = _clean_curve(pts)
        self.assertNotIn(7.9, [p["yield"] for p in out])

    def test_smooth_curve_untouched(self):
        pts = [self.pt(i, 5.5 + i * 0.1) for i in range(1, 10)]
        self.assertEqual(_clean_curve(pts), pts)

    def test_steep_short_end_survives(self):
        # Realistic curve: rises fast 1y->8y then flattens. The July 2026
        # regression: the old median filter cascaded and deleted the whole
        # short end, leaving the chart curve starting at 8y.
        pts = [self.pt(1, 5.52), self.pt(2, 5.51), self.pt(3, 5.81),
               self.pt(4, 5.67), self.pt(6, 6.01), self.pt(8, 6.6),
               self.pt(9, 6.68), self.pt(10, 6.59), self.pt(13, 6.8),
               self.pt(25, 7.17), self.pt(30, 7.33)]
        out = _clean_curve(pts)
        self.assertEqual(out[0]["tenor"], 1)
        self.assertGreaterEqual(len(out), 10)


class ParseDate(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_date("15-Sep-2027"), date(2027, 9, 15))
        self.assertEqual(parse_date("15-09-2027"), date(2027, 9, 15))
        self.assertEqual(parse_date("2027-09-15"), date(2027, 9, 15))
        self.assertIsNone(parse_date("garbage"))
        self.assertIsNone(parse_date(""))


if __name__ == "__main__":
    unittest.main()
