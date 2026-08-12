"""Kite fill -> portfolio lot reconciliation tests (no network, no DB)."""
from typing import Any

import pytest

from backend import db, fills


def order(**kw) -> dict[str, Any]:
    base = {
        "order_id": "1001",
        "isin": "INE000TEST01",
        "issuer_name": "TEST FINCORP",
        "tradingsymbol": "9TF27",
        "exchange": "BSE",
        "transaction_type": "BUY",
        "quantity": 51,
        "filled_quantity": 51,
        "price": 1000.0,
        "average_price": 1001.0,
        "status": "COMPLETE",
        "status_message": None,
        "order_timestamp": "2026-08-11 11:07:14",
    }
    return {**base, **kw}


@pytest.fixture
def env(monkeypatch):
    state = {
        "processed": set(),
        "fills": [],       # (user_id, order_id, lot_id)
        "lots": [],        # add_lot calls
        "notifications": [],
        "next_lot_id": 71,
        "add_lot_error": None,
    }

    def fake_add_lot(user_id, isin, units, price, purchase_date, platform):
        if state["add_lot_error"]:
            raise state["add_lot_error"]
        lot_id = state["next_lot_id"]
        state["next_lot_id"] += 1
        state["lots"].append({"user_id": user_id, "isin": isin,
                              "units": units, "price": price,
                              "purchase_date": purchase_date,
                              "platform": platform, "id": lot_id})
        return {"id": lot_id}

    monkeypatch.setattr(db, "get_processed_order_ids",
                        lambda uid: set(state["processed"]))
    monkeypatch.setattr(db, "record_fill",
                        lambda uid, oid, lot_id: (
                            state["processed"].add(oid),
                            state["fills"].append((uid, oid, lot_id))))
    monkeypatch.setattr(db, "add_lot", fake_add_lot)
    monkeypatch.setattr(db, "add_notification",
                        lambda *a: (state["notifications"].append(a), True)[1])
    return state


def test_buy_complete_creates_lot_and_notification(env):
    created = fills.process_orders(1, [order()])
    assert created == 1
    [lot] = env["lots"]
    assert lot["units"] == 51 and lot["price"] == 1001.0
    assert lot["platform"] == "Zerodha (Kite)"
    assert env["fills"] == [(1, "1001", 71)]
    [(uid, kind, sev, isin, title, body, dedupe)] = env["notifications"]
    assert kind == "order_filled" and isin == "INE000TEST01"
    assert "51 × TEST FINCORP" in title and "₹1,001.00" in title
    assert dedupe == "kite_fill:1001"


def test_idempotent_across_runs(env):
    assert fills.process_orders(1, [order()]) == 1
    assert fills.process_orders(1, [order()]) == 0
    assert len(env["lots"]) == 1
    assert len(env["fills"]) == 1


def test_partial_fill_cancelled_creates_lot_for_filled(env):
    o = order(status="CANCELLED", filled_quantity=4, average_price=999.5)
    assert fills.process_orders(1, [o]) == 1
    assert env["lots"][0]["units"] == 4
    assert env["lots"][0]["price"] == 999.5


def test_sell_recorded_without_lot(env):
    assert fills.process_orders(1, [order(transaction_type="SELL")]) == 0
    assert env["lots"] == [] and env["notifications"] == []
    assert env["fills"] == [(1, "1001", None)]


def test_non_bond_recorded_without_lot(env):
    o = order(isin=None, issuer_name=None, tradingsymbol="GROWW")
    assert fills.process_orders(1, [o]) == 0
    assert env["lots"] == []
    assert env["fills"] == [(1, "1001", None)]


def test_rejected_and_open_orders(env):
    rejected = order(status="REJECTED", filled_quantity=0, average_price=0)
    still_open = order(order_id="1002", status="OPEN")
    assert fills.process_orders(1, [rejected, still_open]) == 0
    # rejected is ledgered; OPEN is left alone for a later poll
    assert env["fills"] == [(1, "1001", None)]


def test_add_lot_failure_still_ledgers(env):
    env["add_lot_error"] = db.WatchlistError(400, "At most 500 lots")
    assert fills.process_orders(1, [order()]) == 0
    assert env["fills"] == [(1, "1001", None)]
    assert env["notifications"] == []


def test_orders_endpoint_reports_new_lots(monkeypatch):
    from fastapi.testclient import TestClient

    from backend import fills as fills_mod
    from backend import kite_connect, queries
    from backend.app import app
    from backend.auth import require_user
    monkeypatch.setattr(kite_connect, "API_KEY", "k")
    monkeypatch.setattr(kite_connect, "API_SECRET", "s")
    monkeypatch.setattr(kite_connect, "token_for", lambda uid: "tok")
    monkeypatch.setattr(kite_connect, "list_orders", lambda uid: [order()])
    monkeypatch.setattr(queries, "symbol_bond_map", lambda: {
        ("BSE", "9TF27"): {"isin": "INE000TEST01",
                           "issuer_name": "TEST FINCORP"}})
    monkeypatch.setattr(fills_mod, "process_orders", lambda uid, orders: 1)
    app.dependency_overrides[require_user] = lambda: 1
    try:
        body = TestClient(app).get("/api/kite/orders").json()
        assert body["new_lots"] == 1
        assert body["orders"][0]["isin"] == "INE000TEST01"
    finally:
        app.dependency_overrides.clear()
