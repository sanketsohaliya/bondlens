"""Portfolio aggregation (pure functions) + API route tests.

The db layer is faked in-memory (CI has no Postgres); the aggregation
math in backend/analytics/portfolio.py is tested directly.
"""
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import auth, db, queries
from backend.analytics import portfolio as pf
from backend.analytics import schedule
from backend.app import app

TODAY = date(2026, 1, 15)


def lot(isin="INE000A00001", units=10.0,
        price: float | None = 1000.0, **kw) -> dict[str, Any]:
    return {"id": kw.get("id", 1), "isin": isin, "units": units,
            "price": price, "purchase_date": kw.get("purchase_date"),
            "platform": kw.get("platform")}


def core(**kw) -> dict[str, Any]:
    return {"issuer_name": "ACME", "credit_rating": "CRISIL: AA",
            "rating_rank": 2, "sector": "Finance",
            "maturity_date": date(2028, 6, 30), "years_to_maturity": 2.5,
            "face_value": 1000.0, "ltp": 1050.0, "ytm": 9.5, **kw}


# --- build_positions / summarize -------------------------------------------

def test_position_math_with_prices():
    lots = [lot(units=10, price=1000), lot(id=2, units=5, price=1030)]
    [p] = pf.build_positions(lots, {"INE000A00001": core()})
    assert p["units"] == 15
    assert p["invested"] == pytest.approx(15150)   # 10x1000 + 5x1030
    assert p["avg_price"] == pytest.approx(1010)   # weighted paid avg
    assert p["value"] == pytest.approx(15750)      # 15 x ltp 1050
    assert p["gain"] == pytest.approx(600)
    assert p["gain_pct"] == pytest.approx(3.96, abs=0.01)
    assert p["rating_grade"] == "AA"


def test_avg_price_null_when_any_lot_unpriced():
    # Legacy rows predating the NOT NULL migration must not fabricate an
    # average from face-value stand-ins.
    lots = [lot(units=10, price=1000), lot(id=2, units=5, price=None)]
    [p] = pf.build_positions(lots, {"INE000A00001": core()})
    assert p["avg_price"] is None
    assert p["gain"] is None


def test_value_fallback_chain_without_market_price():
    lots = [lot(price=990.0)]
    [p] = pf.build_positions(lots, {"INE000A00001": core(ltp=None)})
    assert p["avg_price"] == pytest.approx(990)
    assert p["value"] == pytest.approx(9900)   # marks at paid price
    assert p["gain"] is None                   # no market price -> no P&L


def test_unknown_isin_survives():
    [p] = pf.build_positions([lot(price=1000.0)], {})
    assert p["value"] == pytest.approx(10000)  # marks at paid price
    assert p["issuer_name"] is None and p["rating_grade"] is None


def test_decimal_analytics_from_postgres():
    # psycopg returns numeric columns as Decimal; the math must not blow
    # up mixing them with float lot units/prices (prod regression).
    decimal_core = core(face_value=Decimal("1000"), ltp=Decimal("1050.5"),
                        ytm=Decimal("9.5"),
                        years_to_maturity=Decimal("2.50"))
    [p] = pf.build_positions([lot(units=10, price=1000)],
                             {"INE000A00001": decimal_core})
    s = pf.summarize([p])
    assert p["value"] == pytest.approx(10505)
    assert isinstance(p["ytm"], float)
    assert s["weighted_ytm"] == pytest.approx(9.5)
    assert s["weighted_years"] == pytest.approx(2.5)


def test_summary_weights_by_value():
    positions = pf.build_positions(
        [lot(units=10, price=1000),
         lot(id=2, isin="INE000B00002", units=30, price=1000)],
        {"INE000A00001": core(ytm=8.0, years_to_maturity=2.0),
         "INE000B00002": core(issuer_name="ZETA", ytm=12.0,
                              years_to_maturity=4.0, ltp=1000.0)},
    )
    s = pf.summarize(positions)
    assert s["bonds"] == 2 and s["issuers"] == 2
    assert s["invested"] == pytest.approx(40000)
    assert s["value"] == pytest.approx(10 * 1050 + 30 * 1000)
    # 10500x8 + 30000x12 weighted -> ~10.96
    assert s["weighted_ytm"] == pytest.approx(10.96, abs=0.01)
    assert s["weighted_years"] == pytest.approx(3.48, abs=0.01)


def test_empty_portfolio_summary():
    s = pf.summarize([])
    assert s == {"invested": 0, "value": 0, "gain": None,
                 "weighted_ytm": None, "weighted_years": None,
                 "issuers": 0, "bonds": 0}


# --- monthly_cashflows ------------------------------------------------------

def _flow(due, amount, event="interest", record=None, estimated=False):
    return schedule.Flow(event=event, due_date=due, record_date=record,
                         amount=amount, estimated=estimated)


def _lots(units: float, purchase_date=None) -> list[dict[str, Any]]:
    return [{"units": units, "purchase_date": purchase_date}]


def test_monthly_cashflows_buckets_and_scales():
    flows = {
        "A": [_flow(date(2026, 3, 10), 40.0),
              _flow(date(2026, 3, 20), 10.0, estimated=True),
              _flow(date(2027, 3, 10), 40.0),
              _flow(date(2027, 3, 10), 1000.0, event="redemption")],
        "B": [_flow(date(2026, 3, 5), 25.0)],
    }
    out = pf.monthly_cashflows({"A": _lots(2), "B": _lots(4)}, flows, TODAY,
                               {"A": "875AAA27"})
    assert [m["month"] for m in out] == ["2026-03", "2027-03"]
    m1, m2 = out
    assert m1["interest"] == pytest.approx(40 * 2 + 10 * 2 + 25 * 4)
    assert m1["principal"] == 0 and m1["estimated"] is True
    assert m2["interest"] == pytest.approx(80)
    assert m2["principal"] == pytest.approx(2000)
    assert m2["estimated"] is False

    # Per-bond breakdown: scaled by units, labelled, earliest payment
    # first, and summing exactly to the month's aggregates.
    assert [b["isin"] for b in m1["bonds"]] == ["B", "A"]  # Mar 5 < Mar 10
    b1 = {b["isin"]: b for b in m1["bonds"]}
    assert b1["A"]["interest"] == pytest.approx(100)
    assert b1["A"]["label"] == "875AAA27" and b1["A"]["estimated"] is True
    assert b1["B"]["interest"] == pytest.approx(100)
    assert b1["B"]["label"] == "B" and b1["B"]["estimated"] is False
    assert m2["bonds"][0]["isin"] == "A"  # only payer, principal-heavy
    assert m2["bonds"][0]["principal"] == pytest.approx(2000)
    for m in out:
        assert m["interest"] == pytest.approx(
            sum(b["interest"] for b in m["bonds"]))
        assert m["principal"] == pytest.approx(
            sum(b["principal"] for b in m["bonds"]))

    # Exact payment dates ride along, sorted; the same-day interest +
    # redemption pair collapses to one date.
    assert b1["A"]["dates"] == ["2026-03-10", "2026-03-20"]
    assert b1["B"]["dates"] == ["2026-03-05"]
    assert m2["bonds"][0]["dates"] == ["2027-03-10"]


def test_monthly_cashflows_bond_order_ties_break_by_amount():
    # Same payment day: the larger payer lists first.
    flows = {
        "small": [_flow(date(2026, 3, 10), 10.0)],
        "big": [_flow(date(2026, 3, 10), 90.0)],
    }
    [m] = pf.monthly_cashflows({"small": _lots(1), "big": _lots(1)},
                               flows, TODAY)
    assert [b["isin"] for b in m["bonds"]] == ["big", "small"]


def test_monthly_cashflows_holder_keeps_passed_record_date():
    # Holder semantics: the Feb coupon's record date (Jan 10) passed,
    # but a lot bought before it still receives the payment. Only lots
    # bought after the record date miss it.
    flows = {"A": [_flow(date(2026, 2, 1), 50.0, record=date(2026, 1, 10)),
                   _flow(date(2026, 8, 1), 50.0, record=date(2026, 7, 10))]}
    held = pf.monthly_cashflows({"A": _lots(1, date(2025, 6, 1))}, flows, TODAY)
    assert [m["month"] for m in held] == ["2026-02", "2026-08"]

    bought_late = pf.monthly_cashflows(
        {"A": _lots(1, date(2026, 1, 12))}, flows, TODAY)
    assert [m["month"] for m in bought_late] == ["2026-08"]


def test_monthly_cashflows_mixed_lots_scale_per_record_date():
    # Two lots; only the earlier one receives the Feb coupon.
    flows = {"A": [_flow(date(2026, 2, 1), 50.0, record=date(2026, 1, 10))]}
    lots = [{"units": 2, "purchase_date": date(2025, 6, 1)},
            {"units": 3, "purchase_date": date(2026, 1, 12)}]
    [m] = pf.monthly_cashflows({"A": lots}, flows, TODAY)
    assert m["interest"] == pytest.approx(100)  # 2 units, not 5


def test_monthly_cashflows_no_purchase_date_counts_as_held():
    flows = {"A": [_flow(date(2026, 2, 1), 50.0, record=date(2026, 1, 10))]}
    [m] = pf.monthly_cashflows({"A": _lots(1)}, flows, TODAY)
    assert m["month"] == "2026-02"


def test_trade_on_record_date_misses_the_register():
    # T+1 settlement: trading ON the record date settles a day late.
    flows = {"A": [_flow(date(2026, 2, 1), 50.0, record=date(2026, 1, 10))]}
    on_record = pf.monthly_cashflows(
        {"A": _lots(1, date(2026, 1, 10))}, flows, TODAY)
    assert on_record == []
    day_before = pf.monthly_cashflows(
        {"A": _lots(1, date(2026, 1, 9))}, flows, TODAY)
    assert [m["month"] for m in day_before] == ["2026-02"]


def test_past_flow_bucketed_as_received():
    flows = {"A": [_flow(date(2025, 9, 10), 40.0),
                   _flow(date(2026, 3, 10), 40.0)]}
    out = pf.monthly_cashflows({"A": _lots(2, date(2025, 1, 1))}, flows, TODAY)
    assert [m["month"] for m in out] == ["2025-09", "2026-03"]
    past, future = out
    assert past["interest"] == pytest.approx(80)
    assert past["received_interest"] == pytest.approx(80)
    assert future["received_interest"] == 0 and future["received_principal"] == 0


def test_straddling_month_splits_per_flow():
    # TODAY is mid-month: the 3rd is received, the 25th projected —
    # split exactly at the flow level, not month granularity.
    d_past = TODAY.replace(day=3)
    d_future = TODAY.replace(day=25)
    assert d_past <= TODAY < d_future  # guard the fixture
    flows = {"A": [_flow(d_past, 40.0), _flow(d_future, 60.0)]}
    [m] = pf.monthly_cashflows({"A": _lots(1, date(2025, 1, 1))}, flows, TODAY)
    assert m["interest"] == pytest.approx(100)
    assert m["received_interest"] == pytest.approx(40)


def test_undated_lot_excluded_from_past_income_only():
    flows = {"A": [_flow(date(2025, 9, 10), 40.0),
                   _flow(date(2026, 3, 10), 40.0)]}
    out = pf.monthly_cashflows({"A": _lots(2)}, flows, TODAY)
    # No purchase date: no income history, projections unchanged.
    assert [m["month"] for m in out] == ["2026-03"]
    assert out[0]["interest"] == pytest.approx(80)


def test_past_income_mixed_dated_and_undated_lots():
    flows = {"A": [_flow(date(2025, 9, 10), 40.0)]}
    lots = [{"units": 2, "purchase_date": date(2025, 1, 1)},
            {"units": 3, "purchase_date": None}]
    [m] = pf.monthly_cashflows({"A": lots}, flows, TODAY)
    assert m["received_interest"] == pytest.approx(80)  # dated lot only


def test_past_flow_respects_record_date():
    # Lot bought after the past flow's record date -> not on register.
    flows = {"A": [_flow(date(2025, 9, 10), 40.0, record=date(2025, 8, 25))]}
    out = pf.monthly_cashflows({"A": _lots(1, date(2025, 8, 28))}, flows, TODAY)
    assert out == []


def test_income_summary_sums_buckets():
    flows = {"A": [_flow(date(2025, 9, 10), 40.0),
                   _flow(date(2027, 3, 10), 40.0),
                   _flow(date(2027, 3, 10), 1000.0, event="redemption")]}
    months = pf.monthly_cashflows({"A": _lots(2, date(2025, 1, 1))},
                                  flows, TODAY)
    s = pf.income_summary(months)
    assert s == {"received_interest": 80.0, "received_principal": 0.0,
                 "projected_interest": 80.0, "projected_principal": 2000.0}


def test_income_summary_empty():
    assert pf.income_summary([]) == {
        "received_interest": 0, "received_principal": 0,
        "projected_interest": 0, "projected_principal": 0}


def test_monthly_cashflows_bucket_by_payment_date():
    # Due Sun 1 Feb but paid Fri 30 Jan (business-day adjustment): the
    # flow belongs to January's bucket and shows the January date.
    # TODAY here is 2026-01-15, so it is still projected, not received.
    flow = schedule.Flow(event="interest", due_date=date(2026, 2, 1),
                         amount=50.0, payment_date=date(2026, 1, 30))
    months = pf.monthly_cashflows(
        {"I": _lots(2, date(2025, 6, 1))}, {"I": [flow]}, TODAY)
    [m] = months
    assert m["month"] == "2026-01"
    assert m["bonds"][0]["dates"] == ["2026-01-30"]
    assert m["received_interest"] == 0.0


def test_received_flips_on_payment_date():
    # Due Sat 17 Jan but paid Thu 15 Jan: with TODAY = 15 Jan the money
    # has landed even though the due date is still ahead.
    flow = schedule.Flow(event="interest", due_date=date(2026, 1, 17),
                         amount=50.0, payment_date=date(2026, 1, 15))
    months = pf.monthly_cashflows(
        {"I": _lots(2, date(2025, 6, 1))}, {"I": [flow]}, TODAY)
    [m] = months
    assert m["received_interest"] == 100.0


def test_flow_without_payment_date_unchanged():
    flow = schedule.Flow(event="interest", due_date=date(2026, 2, 1),
                         amount=50.0)
    months = pf.monthly_cashflows(
        {"I": _lots(2, date(2025, 6, 1))}, {"I": [flow]}, TODAY)
    assert months[0]["month"] == "2026-02"
    assert months[0]["bonds"][0]["dates"] == ["2026-02-01"]


# --- yield_on_cost ----------------------------------------------------------

def yoc_lot(units=10.0, price=1000.0, purchase_date=date(2025, 1, 15)):
    return {"units": units, "price": price, "purchase_date": purchase_date}


def test_yield_on_cost_at_par_near_coupon():
    # Bought at par on issue-ish date; 10% annual coupons + face at the
    # end -> XIRR should land near 10%.
    flows = [_flow(date(2026, 1, 15), 100.0),
             _flow(date(2027, 1, 15), 100.0),
             _flow(date(2027, 1, 15), 1000.0, event="redemption")]
    y = pf.yield_on_cost([yoc_lot()], flows, TODAY)
    assert y is not None and 9.5 < y < 10.5


def test_yield_on_cost_discount_buy_beats_coupon():
    flows = [_flow(date(2026, 1, 15), 100.0),
             _flow(date(2027, 1, 15), 100.0),
             _flow(date(2027, 1, 15), 1000.0, event="redemption")]
    y = pf.yield_on_cost([yoc_lot(price=900.0)], flows, TODAY)
    assert y is not None and y > 13


def test_yield_on_cost_excludes_coupons_bought_after_record():
    # Bought after the 2026 record date: that coupon is not the buyer's.
    flows = [_flow(date(2026, 2, 1), 100.0, record=date(2026, 1, 10)),
             _flow(date(2027, 2, 1), 100.0, record=date(2027, 1, 10)),
             _flow(date(2027, 2, 1), 1000.0, event="redemption")]
    with_coupon = pf.yield_on_cost(
        [yoc_lot(purchase_date=date(2026, 1, 5))], flows, TODAY)
    without = pf.yield_on_cost(
        [yoc_lot(purchase_date=date(2026, 1, 20))], flows, TODAY)
    assert with_coupon is not None and without is not None
    assert with_coupon > without


def test_yield_on_cost_none_without_future_flows():
    flows = [_flow(date(2025, 6, 1), 100.0),
             _flow(date(2025, 6, 1), 1000.0, event="redemption")]
    assert pf.yield_on_cost([yoc_lot()], flows, TODAY) is None
    assert pf.yield_on_cost([yoc_lot()], [], TODAY) is None


def test_remaining_payout_scales_future_flows_to_receiving_units():
    flows = [_flow(date(2025, 8, 1), 50.0, record=date(2025, 7, 10)),       # past
             _flow(date(2026, 2, 1), 50.0, record=date(2026, 1, 10)),
             _flow(date(2026, 8, 1), 50.0, record=date(2026, 7, 10), estimated=True),
             _flow(date(2026, 8, 1), 1000.0, event="redemption", record=date(2026, 7, 10))]
    lots = [{"units": 2, "purchase_date": date(2025, 6, 1)},
            {"units": 3, "purchase_date": date(2026, 1, 12)}]   # misses the Feb coupon
    r = pf.remaining_payout(lots, flows, TODAY)
    assert r is not None
    assert r["amount"] == pytest.approx(50 * 2 + 50 * 5 + 1000 * 5)
    assert r["estimated"] is True
    assert r["flows_left"] == 3 and r["flows_total"] == 4
    # Optional exit payouts are not counted; nothing left -> None.
    opt = schedule.Flow(event="put", due_date=date(2026, 5, 1), record_date=None,
                        amount=1000.0, optional=True)
    with_opt = pf.remaining_payout(lots, flows + [opt], TODAY)
    assert with_opt is not None and with_opt["amount"] == r["amount"]
    assert pf.remaining_payout(lots, flows[:1], TODAY) is None
    # An unknown future amount withholds the figure rather than understating it.
    assert pf.remaining_payout(lots, flows + [_flow(date(2026, 6, 1), None)], TODAY) is None


def test_received_so_far_counts_past_flows_for_dated_lots_on_the_register():
    flows = [_flow(date(2025, 8, 1), 50.0, record=date(2025, 7, 10)),
             _flow(date(2025, 12, 1), 300.0, event="redemption", record=date(2025, 11, 10)),
             _flow(date(2026, 2, 1), 50.0, record=date(2026, 1, 10))]   # future
    lots = [{"units": 2, "purchase_date": date(2025, 6, 1)},     # both past flows
            {"units": 3, "purchase_date": date(2025, 7, 20)},    # misses the Aug coupon
            {"units": 5, "purchase_date": None}]                 # undated: never counted
    r = pf.received_so_far(lots, flows, TODAY)
    assert r == {"interest": 100.0, "principal": 1500.0, "payments": 2}
    assert pf.received_so_far(_lots(2, date(2026, 1, 1)), flows, TODAY) is None


def test_next_payment_holder_semantics():
    flows = [_flow(date(2026, 2, 1), 50.0, record=date(2026, 1, 10)),
             _flow(date(2026, 8, 1), 50.0, record=date(2026, 7, 10))]
    # Held before the record date -> Feb coupon is next.
    nxt = pf.next_payment(_lots(2, date(2025, 6, 1)), flows, TODAY)
    assert nxt is not None
    assert nxt["due_date"] == date(2026, 2, 1)
    assert nxt["amount"] == pytest.approx(100)
    # Bought after the Feb record date -> Aug coupon is next.
    nxt = pf.next_payment(_lots(2, date(2026, 1, 12)), flows, TODAY)
    assert nxt is not None
    assert nxt["due_date"] == date(2026, 8, 1)
    # No receivable flow at all.
    assert pf.next_payment(_lots(2), [], TODAY) is None


def test_buyback_options_do_not_inflate_cost_yield_or_income():
    raw = []
    for year, interest in [(2018, 3995), (2021, 6569), (2023, 8682), (2026, 12596)]:
        raw.extend([{"event": "Interest", "due_date": date(year, 12, 12), "amount_payable": interest},
                    {"event": "Full Redemption", "due_date": date(year, 12, 12), "amount_payable": 5000}])
    terms = {"face_value": 5000, "coupon_rate": 8.75, "redemption_date": date(2026, 12, 12),
             "redemption_type": "Partial Redemption By Quantity", "interest_payment_frequency": "Cumulative"}
    as_of = date(2026, 9, 11)
    s = schedule.build("A", raw, terms, as_of)
    lots = [yoc_lot(units=1, price=5000, purchase_date=date(2011, 12, 12))]
    assert pf.yield_on_cost(lots, s.flows, as_of) == 8.74
    months = pf.monthly_cashflows({"A": lots}, {"A": s.flows}, as_of)
    assert [m["month"] for m in months] == ["2026-12"]
    assert pf.income_summary(months) == {"received_interest": 0, "received_principal": 0,
                                       "projected_interest": 12596, "projected_principal": 5000}
    next_payment = pf.next_payment(lots, s.flows, date(2016, 1, 1))
    assert next_payment is not None and next_payment["due_date"] == date(2026, 12, 12)


def test_regular_coupons_remain_when_optional_principal_is_excluded():
    start, call, maturity = date(2025, 1, 1), date(2026, 1, 1), date(2027, 1, 1)
    raw = [{"event": "Interest", "due_date": d, "amount_payable": 100} for d in (call, maturity)]
    raw += [{"event": "Full Redemption", "due_date": d, "amount_payable": 1000} for d in (call, maturity)]
    s = schedule.build("A", raw, {"face_value": 1000, "coupon_rate": 10, "redemption_date": maturity,
                                   "redemption_type": "Full Redemption", "interest_payment_frequency": "Annual",
                                   "call_indicator": "Yes", "call_specified_dates": "01-01-2026"}, start)
    assert sum(f.optional for f in s.flows) == 1
    assert pf.yield_on_cost([yoc_lot(units=1, price=1000, purchase_date=start)], s.flows, date(2026, 2, 1)) == 10


# --- API routes -------------------------------------------------------------

class FakeStore:
    def __init__(self):
        self.next_id = 1
        self.lots: dict[int, dict[str, Any]] = {}

    def get_portfolio_lots(self, user_id: int) -> list[dict[str, Any]]:
        return [dict(v) for v in self.lots.values()
                if v["user_id"] == user_id]

    def add_lot(self, user_id, isin, units, price, purchase_date, platform):
        db._validate_lot(units, price, purchase_date, platform)
        if isin == "INE404NOPE99":
            raise db.WatchlistError(404, "Unknown ISIN")
        lot_id = self.next_id
        self.next_id += 1
        row = {"id": lot_id, "user_id": user_id, "isin": isin, "units": units,
               "price": price, "purchase_date": purchase_date,
               "platform": platform or None}
        self.lots[lot_id] = row
        return {k: v for k, v in row.items() if k != "user_id"}

    def update_lot(self, user_id, lot_id, units, price, purchase_date, platform):
        db._validate_lot(units, price, purchase_date, platform)
        row = self.lots.get(lot_id)
        if row is None or row["user_id"] != user_id:
            raise db.WatchlistError(404, "Lot not found")
        row.update(units=units, price=price, purchase_date=purchase_date,
                   platform=platform or None)
        return {k: v for k, v in row.items() if k != "user_id"}

    def delete_lot(self, user_id, lot_id):
        row = self.lots.get(lot_id)
        if row is None or row["user_id"] != user_id:
            raise db.WatchlistError(404, "Lot not found")
        del self.lots[lot_id]


@pytest.fixture
def client(monkeypatch):
    store = FakeStore()
    for fn in ("get_portfolio_lots", "add_lot", "update_lot", "delete_lot"):
        monkeypatch.setattr(db, fn, getattr(store, fn))
    monkeypatch.setattr(queries, "analytics_row", lambda isin, _d: core())
    monkeypatch.setattr(queries, "bond_cashflows", lambda isin: [])
    monkeypatch.setattr(queries, "bond_terms", lambda isin: None)
    monkeypatch.setattr(queries, "exchange_notice_rows", lambda isins=None, **k: {})
    monkeypatch.setattr(queries, "ratings_for_isins", lambda isins: [])
    app.dependency_overrides[auth.require_user] = lambda: 1
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_lot_crud_flow(client):
    r = client.post("/api/portfolio/lots", json={
        "isin": "ine000a00001", "units": 10, "price": 1010.5,
        "purchase_date": "2025-06-01", "platform": "IndiaBonds"})
    assert r.status_code == 200
    lot_id = r.json()["id"]
    assert r.json()["isin"] == "INE000A00001"  # normalized

    r = client.get("/api/portfolio")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["bonds"] == 1
    assert body["income"] == {
        "received_interest": 0, "received_principal": 0,
        "projected_interest": 0, "projected_principal": 0}
    [p] = body["positions"]
    assert p["units"] == 10 and p["lots"][0]["platform"] == "IndiaBonds"

    r = client.patch(f"/api/portfolio/lots/{lot_id}",
                     json={"units": 12, "price": 1000,
                           "purchase_date": "2025-06-01"})
    assert r.status_code == 200 and r.json()["units"] == 12

    assert client.delete(f"/api/portfolio/lots/{lot_id}").status_code == 200
    assert client.get("/api/portfolio").json()["positions"] == []


def test_lot_validation(client):
    ok = {"units": 5, "price": 1000, "purchase_date": "2025-06-01"}
    # Units, price and purchase date are hard requirements (Pydantic 422).
    bad = [{"isin": "INE000A00001", **ok, "units": 0},
           {"isin": "INE000A00001", **ok, "price": -1},
           {"isin": "INE000A00001", "units": 5, "price": 1000},   # no date
           {"isin": "INE000A00001", "units": 5,
            "purchase_date": "2025-06-01"},                        # no price
           {"isin": "INE404NOPE99", **ok}]
    for body, status in zip(bad, (422, 422, 422, 422, 404), strict=True):
        assert client.post("/api/portfolio/lots", json=body).status_code == status
    assert client.patch("/api/portfolio/lots/999", json=ok).status_code == 404
    assert client.delete("/api/portfolio/lots/999").status_code == 404
