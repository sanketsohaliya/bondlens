"""Reconcile filled Kite orders into portfolio holdings.

A BUY order on a bond in the universe that reaches a terminal state
with fills becomes a portfolio lot (units = filled quantity, price =
average fill price, purchase date = broker date) plus a bell notification.
Everything else that terminates — sells, non-bond instruments, zero
fills — is recorded in the kite_fills ledger so it is never examined
again. Lot, notification and ledger writes share a locked transaction;
failed imports remain retryable instead of silently losing holdings.
"""
import math
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from . import db

TERMINAL = {"COMPLETE", "CANCELLED", "REJECTED"}
PLATFORM = "Zerodha (Kite)"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _execution_date(order: dict[str, Any]) -> date:
    stamp = order.get("exchange_update_timestamp") or order.get("exchange_timestamp")
    if not stamp and order.get("variety") != "amo":
        stamp = order.get("order_timestamp")
    try:
        value = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        result = value.astimezone(ZoneInfo("Asia/Kolkata")).date() if value.tzinfo else value.date()
    except (ValueError, TypeError):
        raise db.WatchlistError(400, "Broker execution date is unavailable; import will retry") from None
    if result > datetime.now(ZoneInfo("Asia/Kolkata")).date():
        raise db.WatchlistError(400, "Broker execution date is in the future; import will retry")
    return result


def _fill_lot(user_id: int, order: dict[str, Any], conn) -> int:
    """Create a lot in the reconciliation transaction. Validation failures
    are reported to the user without marking the order reconciled,
    so a later retry can recover once the input or lot limit is fixed."""
    filled, avg = _number(order.get("filled_quantity")), _number(order.get("average_price"))
    if filled is None or avg is None or filled <= 0 or avg <= 0:
        raise db.WatchlistError(400, "Broker fill quantity or average price is unavailable; import will retry")
    lot = db.add_lot(user_id, order["isin"], filled, avg, _execution_date(order), PLATFORM, conn=conn)
    return lot["id"]


def process_orders(user_id: int, orders: list[dict[str, Any]]) -> int:
    """Reconcile terminal orders; return the number of committed lots."""
    terminal = [o for o in orders if o.get("status") in TERMINAL and o.get("order_id")]
    if not terminal:
        return 0
    done = db.get_processed_order_ids(user_id)
    created = 0
    for o in terminal:
        order_id = str(o["order_id"])
        if order_id in done:
            continue
        with db.fill_transaction(user_id, order_id) as conn:
            if conn is None:
                continue
            lot_id = None
            filled = _number(o.get("filled_quantity"))
            if o.get("transaction_type") == "BUY" and o.get("isin") and filled != 0:
                name = o.get("issuer_name") or o.get("tradingsymbol") or o["isin"]
                try:
                    lot_id = _fill_lot(user_id, o, conn)
                except db.WatchlistError as exc:
                    db.add_notification(user_id, "order_import_failed", "warn", o["isin"],
                                        f"Filled BUY not imported — {name}", exc.detail,
                                        f"kite_import_failed:{order_id}", conn=conn)
                    continue
                assert filled is not None
                avg = float(o["average_price"])
                requested = _number(o.get("quantity"))
                partial = requested is not None and filled < requested
                title = (f"BUY partially filled: {filled:g} of {requested:g} units — {name} @ ₹{avg:,.2f}"
                         if partial else f"BUY filled: {filled:g} × {name} @ ₹{avg:,.2f}")
                body = (f"{o.get('exchange') or 'Exchange'} order {order_id}; status {o['status']}. "
                        f"{filled:g} filled units added to your portfolio using broker date {_execution_date(o)}.")
                db.add_notification(user_id, "order_filled", "info", o["isin"], title, body,
                                    f"kite_fill:{order_id}", conn=conn)
            db.record_fill(user_id, order_id, lot_id, conn=conn)
        done.add(order_id)
        created += lot_id is not None
    return created
