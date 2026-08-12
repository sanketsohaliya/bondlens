"""Reconcile filled Kite orders into portfolio holdings.

A BUY order on a bond in the universe that reaches a terminal state
with fills becomes a portfolio lot (units = filled quantity, price =
average fill price, purchase date = today) plus a bell notification.
Everything else that terminates — sells, non-bond instruments, zero
fills — is recorded in the kite_fills ledger so it is never examined
again. The (user_id, order_id) primary key makes processing idempotent
across the 15s order polls and the market-hours sweep job, which both
feed this same function.
"""
from datetime import date
from typing import Any

from . import db

TERMINAL = {"COMPLETE", "CANCELLED", "REJECTED"}
PLATFORM = "Zerodha (Kite)"


def _fill_lot(user_id: int, order: dict[str, Any]) -> int | None:
    """Create the lot for a filled bond BUY; None when add_lot refuses
    (unknown ISIN, lot cap, …) — the order still gets ledgered so we
    don't retry forever."""
    try:
        lot = db.add_lot(
            user_id,
            order["isin"],
            float(order["filled_quantity"]),
            float(order["average_price"]),
            date.today(),
            PLATFORM,
        )
        return lot["id"]
    except db.WatchlistError:
        return None


def process_orders(user_id: int, orders: list[dict[str, Any]]) -> int:
    """Reconcile today's orders; returns how many lots were created."""
    terminal = [o for o in orders
                if o.get("status") in TERMINAL and o.get("order_id")]
    if not terminal:
        return 0
    done = db.get_processed_order_ids(user_id)
    created = 0
    for o in terminal:
        order_id = str(o["order_id"])
        if order_id in done:
            continue
        lot_id = None
        filled = o.get("filled_quantity") or 0
        avg = o.get("average_price") or 0
        if (o.get("transaction_type") == "BUY" and o.get("isin")
                and filled > 0 and avg > 0):
            lot_id = _fill_lot(user_id, o)
            if lot_id is not None:
                created += 1
                name = o.get("issuer_name") or o.get("tradingsymbol") or o["isin"]
                db.add_notification(
                    user_id, "order_filled", "info", o["isin"],
                    f"Order filled: {filled:g} × {name} @ "
                    f"₹{avg:,.2f}",
                    "Added to your portfolio automatically.",
                    f"kite_fill:{order_id}",
                )
        db.record_fill(user_id, order_id, lot_id)
    return created
