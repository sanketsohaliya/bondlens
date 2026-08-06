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
    assert p["value"] == pytest.approx(15750)      # 15 x ltp 1050
    assert p["gain"] == pytest.approx(600)
    assert p["gain_pct"] == pytest.approx(3.96, abs=0.01)
    assert p["rating_grade"] == "AA"


def test_no_fake_pnl_without_lot_price():
    lots = [lot(price=None)]
    [p] = pf.build_positions(lots, {"INE000A00001": core()})
    assert p["invested"] == 10000       # falls back to face value
    assert p["value"] == 10500          # marks at ltp
    assert p["gain"] is None            # but no P&L claimed
    assert p["gain_pct"] is None


def test_value_fallback_chain_without_market_price():
    lots = [lot(price=990.0)]
    [p] = pf.build_positions(lots, {"INE000A00001": core(ltp=None)})
    assert p["value"] == pytest.approx(9900)   # marks at paid price
    assert p["gain"] is None                   # no market price -> no P&L


def test_unknown_isin_survives():
    [p] = pf.build_positions([lot(price=None)], {})
    assert p["invested"] is None and p["value"] is None
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


def test_monthly_cashflows_buckets_and_scales():
    flows = {
        "A": [_flow(date(2026, 3, 10), 40.0),
              _flow(date(2026, 3, 20), 10.0, estimated=True),
              _flow(date(2027, 3, 10), 40.0),
              _flow(date(2027, 3, 10), 1000.0, event="redemption")],
        "B": [_flow(date(2026, 3, 5), 25.0)],
    }
    out = pf.monthly_cashflows({"A": 2, "B": 4}, flows, TODAY,
                               {"A": "875AAA27"})
    assert [m["month"] for m in out] == ["2026-03", "2027-03"]
    m1, m2 = out
    assert m1["interest"] == pytest.approx(40 * 2 + 10 * 2 + 25 * 4)
    assert m1["principal"] == 0 and m1["estimated"] is True
    assert m2["interest"] == pytest.approx(80)
    assert m2["principal"] == pytest.approx(2000)
    assert m2["estimated"] is False

    # Per-bond breakdown: scaled by units, labelled, largest payer first,
    # and summing exactly to the month's aggregates.
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


def test_monthly_cashflows_record_date_excludes_sellers_coupon():
    flows = {"A": [_flow(date(2026, 2, 1), 50.0, record=date(2026, 1, 10)),
                   _flow(date(2026, 8, 1), 50.0, record=date(2026, 7, 10))]}
    out = pf.monthly_cashflows({"A": 1}, flows, TODAY)
    # Feb coupon's record date (Jan 10) already passed -> seller's money.
    assert [m["month"] for m in out] == ["2026-08"]


# --- API routes -------------------------------------------------------------

class FakeStore:
    def __init__(self):
        self.next_id = 1
        self.lots: dict[int, dict[str, Any]] = {}

    def get_portfolio_lots(self, user_id: int) -> list[dict[str, Any]]:
        return [dict(v) for v in self.lots.values()
                if v["user_id"] == user_id]

    def add_lot(self, user_id, isin, units, price, purchase_date, platform):
        db._validate_lot(units, price, platform)
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
        db._validate_lot(units, price, platform)
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
    [p] = body["positions"]
    assert p["units"] == 10 and p["lots"][0]["platform"] == "IndiaBonds"

    r = client.patch(f"/api/portfolio/lots/{lot_id}",
                     json={"units": 12, "price": 1000})
    assert r.status_code == 200 and r.json()["units"] == 12

    assert client.delete(f"/api/portfolio/lots/{lot_id}").status_code == 200
    assert client.get("/api/portfolio").json()["positions"] == []


def test_lot_validation(client):
    bad = [{"isin": "INE000A00001", "units": 0},
           {"isin": "INE000A00001", "units": 5, "price": -1},
           {"isin": "INE404NOPE99", "units": 5}]
    for body, status in zip(bad, (400, 400, 404), strict=True):
        assert client.post("/api/portfolio/lots", json=body).status_code == status
    assert client.patch("/api/portfolio/lots/999",
                        json={"units": 1}).status_code == 404
    assert client.delete("/api/portfolio/lots/999").status_code == 404
