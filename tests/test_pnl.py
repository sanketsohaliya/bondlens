"""Ground-truth tests for portfolio P&L math (xirr, received flows, holding P&L).

Every expected value is hand-computable. Run: python -m unittest discover tests -v
"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bondlens.pnl import holding_pnl, received_flows
from bondlens.ytm import xirr

TODAY = date(2026, 7, 4)


def flow(due, amount, event="Interest", record=None):
    return {"due_date": due, "record_date": record, "event": event,
            "amount": amount}


class Xirr(unittest.TestCase):
    def test_simple_one_year_double(self):
        # -1000 today, +1100 in 365 days: 10%
        flows = [(date(2025, 7, 4), -1000.0), (date(2026, 7, 4), 1100.0)]
        self.assertAlmostEqual(xirr(flows), 10.0, places=2)

    def test_with_interim_coupon(self):
        # -1000, +80 after 1y, +1080 after 2y: par bond, 8%
        flows = [
            (date(2024, 7, 4), -1000.0),
            (date(2025, 7, 4), 80.0),
            (date(2026, 7, 4), 1080.0),
        ]
        self.assertAlmostEqual(xirr(flows), 8.0, delta=0.03)

    def test_loss(self):
        # -1000, +900 after 1y: -10%
        flows = [(date(2025, 7, 4), -1000.0), (date(2026, 7, 4), 900.0)]
        self.assertAlmostEqual(xirr(flows), -10.0, delta=0.02)

    def test_no_sign_change_is_none(self):
        self.assertIsNone(xirr([(date(2025, 7, 4), 100.0), (date(2026, 7, 4), 100.0)]))

    def test_single_flow_is_none(self):
        self.assertIsNone(xirr([(date(2025, 7, 4), -100.0)]))


class ReceivedFlows(unittest.TestCase):
    def test_window(self):
        buy = date(2025, 1, 1)
        flows = [
            flow("15-06-2024", 80.0),                       # before purchase
            flow("15-06-2025", 80.0),                       # received
            flow("15-06-2026", 80.0),                       # received
            flow("15-06-2027", 80.0),                       # still in the future
        ]
        got = received_flows(flows, buy, TODAY)
        self.assertEqual([f["due_date"] for f in got],
                         ["15-06-2025", "15-06-2026"])

    def test_record_date_before_buy_excludes(self):
        # Paid after purchase, but record date passed before the buy —
        # the seller keeps it.
        buy = date(2025, 6, 10)
        flows = [flow("15-06-2025", 80.0, record="01-06-2025")]
        self.assertEqual(received_flows(flows, buy, TODAY), [])

    def test_blank_amounts_skipped(self):
        buy = date(2025, 1, 1)
        flows = [flow("15-06-2025", None)]
        self.assertEqual(received_flows(flows, buy, TODAY), [])


class HoldingPnl(unittest.TestCase):
    def holding(self, **kw):
        base = {"isin": "INE000A00000", "qty": 10.0, "buy_price": 980.0,
                "buy_date": "2025-01-01"}
        base.update(kw)
        return base

    def test_full_figures(self):
        # 10 units bought at 980 just before the schedule's first flow;
        # one 80 coupon received; LTP 1005.
        flows = [
            flow("15-06-2025", 80.0),
            flow("15-06-2027", 80.0),
            flow("15-06-2027", 1000.0, "Full Redemption"),
        ]
        figs = holding_pnl(self.holding(buy_date="2025-06-01"),
                           {"ltp": 1005.0}, flows, True, TODAY)
        self.assertEqual(figs["invested"], 9800.0)
        self.assertEqual(figs["market_value"], 10050.0)
        self.assertEqual(figs["realized_interest"], 800.0)
        self.assertEqual(figs["realized_principal"], 0.0)
        self.assertEqual(figs["realized"], 800.0)
        self.assertEqual(figs["unrealized"], 250.0)
        self.assertEqual(figs["total_pnl"], 1050.0)
        self.assertEqual(figs["warnings"], [])
        # 1050 gained on 9800 over ~1.1 years: annualized must be positive
        # and below the simple 10.7% total-return figure at one year.
        self.assertIsNotNone(figs["xirr"])
        self.assertGreater(figs["xirr"], 5.0)
        self.assertLess(figs["xirr"], 10.8)

    def test_schedule_coverage_gap_warns(self):
        # NSDL only lists payments still outstanding at fetch time. Bought
        # Jan 2025, but the cached schedule starts Feb 2027: the Feb 2025
        # and Feb 2026 coupons are invisible — must be flagged.
        flows = [
            flow("03-02-2027", 73.0),
            flow("03-02-2041", 73.0),
            flow("03-02-2041", 1000.0, "Full Redemption"),
        ]
        figs = holding_pnl(self.holding(), {"ltp": 989.0}, flows, True, TODAY)
        self.assertEqual(figs["realized"], 0.0)
        self.assertTrue(any("cached schedule starts" in w for w in figs["warnings"]))

    def test_recent_buy_no_coverage_warning(self):
        # Bought days before the first cached flow: nothing could have been
        # missed, so no warning.
        flows = [flow("15-06-2025", 80.0)]
        figs = holding_pnl(self.holding(buy_date="2025-06-01"),
                           {"ltp": 1005.0}, flows, True, TODAY)
        self.assertEqual(figs["warnings"], [])

    def test_matured_bond_is_closed_not_unknown(self):
        # Fully redeemed bond drops out of the NSE feed: the position is
        # closed, not missing data. P&L is final, XIRR uses actual receipts.
        flows = [
            flow("15-06-2025", 80.0),
            flow("15-06-2026", 80.0),
            flow("15-06-2026", 1000.0, "Full Redemption"),
        ]
        figs = holding_pnl(self.holding(buy_date="2025-06-01"),
                           None, flows, True, TODAY)
        self.assertTrue(figs["closed"])
        self.assertEqual(figs["market_value"], 0.0)
        self.assertEqual(figs["realized"], 11600.0)  # 2×80 + 1000, ×10 units
        self.assertIsNone(figs["unrealized"])        # nothing left unrealized
        self.assertEqual(figs["total_pnl"], 1800.0)  # 11600 − 9800
        self.assertIsNotNone(figs["xirr"])
        self.assertNotIn("not in the latest NSE snapshot — no market price",
                         figs["warnings"])

    def test_future_flows_not_closed(self):
        # Same shape but redemption still ahead: missing price is a real
        # data problem, not maturity.
        flows = [
            flow("15-06-2025", 80.0),
            flow("15-06-2027", 80.0),
            flow("15-06-2027", 1000.0, "Full Redemption"),
        ]
        figs = holding_pnl(self.holding(buy_date="2025-06-01"),
                           None, flows, True, TODAY)
        self.assertFalse(figs["closed"])
        self.assertIsNone(figs["market_value"])
        self.assertIn("not in the latest NSE snapshot — no market price",
                      figs["warnings"])

    def test_past_flows_without_redemption_not_closed(self):
        # All cached flows past but no principal event: the schedule is
        # incomplete, don't declare the position closed.
        flows = [flow("15-06-2025", 80.0), flow("15-06-2026", 80.0)]
        figs = holding_pnl(self.holding(buy_date="2025-06-01"),
                           None, flows, True, TODAY)
        self.assertFalse(figs["closed"])

    def test_amortizer_principal_not_a_loss(self):
        # A price-only P&L view: 300 principal repaid looks like a crash.
        # Here it lands in realized_principal instead.
        flows = [
            flow("15-06-2025", 90.0),
            flow("15-06-2025", 300.0, "Partial Redemption"),
            flow("15-06-2027", 63.0),
            flow("15-06-2027", 700.0, "Full Redemption"),
        ]
        figs = holding_pnl(self.holding(buy_price=1000.0),
                           {"ltp": 705.0}, flows, True, TODAY)
        self.assertEqual(figs["realized_principal"], 3000.0)
        self.assertEqual(figs["realized_interest"], 900.0)
        # 7050 (mv) + 3900 (realized) - 10000 (cost) = +950, not a loss
        self.assertEqual(figs["total_pnl"], 950.0)

    def test_no_buy_date(self):
        figs = holding_pnl(self.holding(buy_date=None), {"ltp": 1005.0},
                           [flow("15-06-2025", 80.0)], True, TODAY)
        self.assertIsNone(figs["realized"])
        self.assertIsNone(figs["xirr"])
        self.assertIsNone(figs["total_pnl"])
        self.assertEqual(figs["unrealized"], 250.0)
        self.assertTrue(any("buy date" in w for w in figs["warnings"]))

    def test_no_snapshot_row(self):
        figs = holding_pnl(self.holding(), None, [], False, TODAY)
        self.assertIsNone(figs["market_value"])
        self.assertIsNone(figs["total_pnl"])
        self.assertTrue(any("snapshot" in w for w in figs["warnings"]))

    def test_schedule_mismatch_warns(self):
        figs = holding_pnl(self.holding(), {"ltp": 1005.0},
                           [flow("15-06-2025", 80.0)], False, TODAY)
        self.assertTrue(any("face value" in w for w in figs["warnings"]))


if __name__ == "__main__":
    unittest.main()
