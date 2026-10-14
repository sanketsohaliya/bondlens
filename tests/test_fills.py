"""Kite fill -> portfolio lot reconciliation tests (no network, no DB)."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
from threading import RLock
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

    lock = RLock()

    @contextmanager
    def transaction(uid, oid):
        with lock:
            keys = ("processed", "fills", "lots", "notifications", "next_lot_id")
            before = deepcopy({key: state[key] for key in keys})
            try:
                yield None if (uid, oid) in state["processed"] else object()
            except Exception:
                state.update(before)
                raise

    def record(uid, oid, lot_id, **kwargs):
        if state.get("fail_stage") == "ledger":
            raise RuntimeError("simulated ledger failure")
        state["processed"].add((uid, oid))
        state["fills"].append((uid, oid, lot_id))

    def notify(*args, **kwargs):
        if state.get("fail_stage") == "notification":
            raise RuntimeError("simulated notification failure")
        state["notifications"].append(args)
        return True

    def fake_add_lot(user_id, isin, units, price, purchase_date, platform, **kwargs):
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
                        lambda uid: {oid for user, oid in state["processed"] if user == uid})
    monkeypatch.setattr(db, "fill_transaction", transaction, raising=False)
    monkeypatch.setattr(db, "record_fill", record)
    monkeypatch.setattr(db, "add_lot", fake_add_lot)
    monkeypatch.setattr(db, "add_notification", notify)
    state["pushes"] = []
    monkeypatch.setattr(fills.opportunity_rules, "get_topic", lambda uid: state.get("topic", "t-abcdef"))
    monkeypatch.setattr(fills.ntfy, "push",
                        lambda topic, title, body, **kw: state["pushes"].append((topic, title, body)) or 1)
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
    assert title == "Filled: 51 × TEST FINCORP — ₹51,051" and body.startswith("avg ₹1,001.00 on ")
    assert dedupe == "kite_fill:1001"
    assert env["pushes"] == [("t-abcdef", title, body)]   # the phone hears the same text


def test_fill_push_is_skipped_without_a_topic_and_never_repeats(env):
    env["topic"] = None
    assert fills.process_orders(1, [order()]) == 1
    assert env["pushes"] == []
    env["topic"] = "t-abcdef"
    assert fills.process_orders(1, [order()]) == 0   # already reconciled: no second notification, no push
    assert env["pushes"] == []


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


def test_add_lot_failure_is_reported_and_remains_retryable(env):
    env["add_lot_error"] = db.WatchlistError(400, "At most 500 lots")
    assert fills.process_orders(1, [order()]) == 0
    assert env["fills"] == []
    assert env["notifications"][0][1] == "order_import_failed"
    env["add_lot_error"] = None
    assert fills.process_orders(1, [order()]) == 1


@pytest.mark.parametrize("stage", ["ledger", "notification"])
def test_failed_reconciliation_rolls_back_lot_and_retry_creates_one(env, stage):
    env["fail_stage"] = stage
    with pytest.raises(RuntimeError):
        fills.process_orders(1, [order()])
    assert env["lots"] == [] and env["fills"] == [] and env["notifications"] == []
    env["fail_stage"] = None
    assert fills.process_orders(1, [order(), order()]) == 1
    assert len(env["lots"]) == 1


def test_fill_uses_the_execution_date_and_describes_partial_terminal_fill(env):
    fills.process_orders(1, [order(status="CANCELLED", filled_quantity=4)])
    assert env["lots"][0]["purchase_date"] == date(2026, 8, 11)
    assert "4 of 51" in env["notifications"][0][4]
    assert "CANCELLED" in env["notifications"][0][5]


def test_exchange_date_takes_precedence_for_after_market_orders(env):
    fills.process_orders(1, [order(variety="amo", exchange_timestamp="2026-08-12 09:15:00")])
    assert env["lots"][0]["purchase_date"] == date(2026, 8, 12)


@pytest.mark.parametrize("changes", [
    {"variety": "amo"}, {"average_price": float("nan")},
    {"filled_quantity": -1}, {"order_timestamp": None},
    {"order_timestamp": "2999-01-01 10:00:00"},
])
def test_untrustworthy_fill_inputs_are_not_silently_ledgered(env, changes):
    assert fills.process_orders(1, [order(**changes)]) == 0
    assert not env["lots"] and not env["fills"]
    assert env["notifications"][0][1] == "order_import_failed"


def test_database_locks_user_before_rechecking_order(monkeypatch):
    calls = []

    class Cursor:
        def fetchone(self):
            return {"id": 1} if len(calls) == 1 else None

    class Connection:
        def execute(self, sql, params):
            calls.append((sql, params))
            return Cursor()

    conn = Connection()

    @contextmanager
    def connection():
        yield conn

    monkeypatch.setattr(db, "_conn", connection)
    with db.fill_transaction(7, "123") as actual:
        assert actual is conn
    assert "FOR UPDATE" in calls[0][0] and calls[0][1] == (7,)
    assert "kite_fills" in calls[1][0] and calls[1][1] == (7, "123")


def test_same_order_id_is_scoped_to_user(env):
    assert fills.process_orders(1, [order()]) == 1
    assert fills.process_orders(2, [order()]) == 1
    assert len(env["lots"]) == 2


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
