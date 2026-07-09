"""Per-holding P&L: realized income, unrealized mark-to-market, and XIRR.

Why this exists: broker P&L views are price-only — they ignore coupons
received and show amortizer principal repayments as fake losses. Here every
rupee is accounted for:

- realized income = coupons + principal instalments whose record date
  (fallback: due date) was after the buy date and which have been paid out
  by today,
- unrealized = current market value (qty x dirty LTP) minus cost,
- XIRR over the full dated history: -cost at buy, +each receipt on its due
  date, +market value today.

Figures that need a buy date/price are None when the holding lacks them.
"""
from datetime import date
from typing import Dict, List, Optional

from .ytm import parse_date, xirr

_PRINCIPAL_EVENTS = ("REDEM", "MATURITY", "PRINCIPAL")

# A schedule whose earliest flow is within this many days of the buy date is
# considered complete: no payout could plausibly have fallen in the gap.
_COVERAGE_GRACE_DAYS = 35


def _is_principal(event: Optional[str]) -> bool:
    ev = (event or "").upper()
    return any(k in ev for k in _PRINCIPAL_EVENTS)


def received_flows(cashflows: List[Dict], buy_date: date,
                   today: Optional[date] = None) -> List[Dict]:
    """Flows the holder actually received between purchase and today.

    A flow belongs to this holder if its record date (fallback: due date)
    is after the buy date — buying on/after the record date means the
    seller keeps that payout. It has been received once its due date has
    passed.
    """
    today = today or date.today()
    out = []
    for f in cashflows:
        due = parse_date(f.get("due_date"))
        if due is None or due > today:
            continue
        record = parse_date(f.get("record_date")) or due
        if record <= buy_date:
            continue
        if f.get("amount"):
            out.append(f)
    return out


def holding_pnl(holding: Dict, snapshot_row: Optional[Dict],
                cashflows: List[Dict], schedule_ok: bool,
                today: Optional[date] = None) -> Dict:
    """Compute P&L figures for one holding. Never raises on missing data —
    figures that can't be computed come back None, with `warnings` explaining
    what to distrust.
    """
    today = today or date.today()
    qty = holding["qty"]
    buy_price = holding.get("buy_price")
    buy_date = parse_date(holding.get("buy_date") or "")
    ltp = snapshot_row.get("ltp") if snapshot_row else None

    warnings = []
    if snapshot_row is None:
        warnings.append("not in the latest NSE snapshot — no market price")
    if not schedule_ok:
        warnings.append("payment schedule doesn't match face value — income figures unreliable")
    if not cashflows:
        warnings.append("no payment schedule cached — realized income unknown")
    if buy_date is None:
        warnings.append("no buy date — realized income and XIRR need one")

    invested = round(qty * buy_price, 2) if buy_price else None
    market_value = round(qty * ltp, 2) if ltp else None

    realized_interest = realized_principal = None
    received = []
    if buy_date is not None and cashflows:
        received = received_flows(cashflows, buy_date, today)
        # NSDL publishes only the payments still outstanding at fetch time, so
        # the cached schedule's coverage starts at its earliest due date.
        # Payouts due between the buy date and that point are invisible here.
        earliest = min(
            (d for d in (parse_date(f.get("due_date")) for f in cashflows) if d),
            default=None,
        )
        if earliest is not None:
            uncovered_until = min(earliest, today)
            if (uncovered_until - buy_date).days > _COVERAGE_GRACE_DAYS:
                warnings.append(
                    "cached schedule starts %s — payouts due between your buy "
                    "date and then aren't counted in income"
                    % earliest.strftime("%d-%b-%Y"))
        realized_interest = round(
            qty * sum(f["amount"] for f in received if not _is_principal(f.get("event"))), 2)
        realized_principal = round(
            qty * sum(f["amount"] for f in received if _is_principal(f.get("event"))), 2)

    realized = (
        round(realized_interest + realized_principal, 2)
        if realized_interest is not None else None
    )
    unrealized = (
        round(market_value - invested, 2)
        if market_value is not None and invested is not None else None
    )
    total = (
        round((realized or 0.0) + market_value - invested, 2)
        if market_value is not None and invested is not None
           and (realized is not None or not cashflows) else None
    )

    rate = None
    if buy_date is not None and invested and market_value is not None:
        dated = [(buy_date, -invested)]
        dated += [(parse_date(f["due_date"]), qty * f["amount"]) for f in received]
        dated.append((today, market_value))
        rate = xirr(dated)

    return {
        "invested": invested,
        "market_value": market_value,
        "realized_interest": realized_interest,
        "realized_principal": realized_principal,
        "realized": realized,
        "unrealized": unrealized,
        "total_pnl": total,
        "xirr": rate,
        "receipts": len(received),
        "warnings": warnings,
    }
