"""Portfolio aggregation — pure functions over lots + precomputed analytics.

No database access here: app.py fetches lots, analytics rows and schedules,
and this module does the math (positions, summary, monthly cashflows), so
everything is unit-testable without Postgres.

Valuation fallbacks (documented in the API):
  value    = units x (ltp | avg paid price | face value)
  invested = units x (avg paid price | face value)
Gain is only reported when every lot in the position has a price — face
value fallbacks must not manufacture fake P&L.
"""
import math
from datetime import date
from typing import Any

from . import ratings, yields
from .schedule import Flow


def _f(v: Any) -> float | None:
    """Postgres numeric columns arrive as Decimal; summary math needs float."""
    return float(v) if v is not None else None


def _weighted_price(lots: list[dict[str, Any]]) -> float | None:
    """Units-weighted average paid price; None unless every lot has one."""
    if any(lot["price"] is None for lot in lots):
        return None
    units = sum(lot["units"] for lot in lots)
    return sum(lot["units"] * lot["price"] for lot in lots) / units


def build_positions(lots: list[dict[str, Any]],
                    core_by_isin: dict[str, dict[str, Any]],
                    ) -> list[dict[str, Any]]:
    """One position per held ISIN, biggest value first."""
    by_isin: dict[str, list[dict[str, Any]]] = {}
    for lot in lots:
        by_isin.setdefault(lot["isin"], []).append(lot)

    positions = []
    for isin, isin_lots in by_isin.items():
        core = core_by_isin.get(isin) or {}
        units = sum(lot["units"] for lot in isin_lots)
        face = _f(core.get("face_value"))
        ltp = _f(core.get("ltp"))
        paid = _weighted_price(isin_lots)

        basis = paid if paid is not None else face
        mark = ltp if ltp is not None else basis
        invested = units * basis if basis is not None else None
        value = units * mark if mark is not None else None
        # Real P&L needs real inputs: every lot priced AND a market price.
        gain = gain_pct = None
        if paid is not None and ltp is not None and invested and value is not None:
            gain = round(value - invested, 2)
            gain_pct = round(gain / invested * 100, 2)

        rank = core.get("rating_rank")
        positions.append({
            "isin": isin,
            "avg_price": round(paid, 2) if paid is not None else None,
            "issuer_name": core.get("issuer_name"),
            "credit_rating": core.get("credit_rating"),
            "rating_rank": rank,
            "rating_grade": ratings.RATING_ORDER[rank]
                            if rank is not None else None,
            "rating_change": core.get("rating_change"),
            "rating_prev": core.get("rating_prev"),
            "rating_changed_at": core.get("rating_changed_at"),
            "sector": core.get("sector"),
            "maturity_date": core.get("maturity_date"),
            "years_to_maturity": _f(core.get("years_to_maturity")),
            "face_value": face,
            "ltp": ltp,
            "ytm": _f(core.get("ytm")),
            "units": units,
            "invested": round(invested, 2) if invested is not None else None,
            "value": round(value, 2) if value is not None else None,
            "gain": gain,
            "gain_pct": gain_pct,
            "lots": isin_lots,
        })
    positions.sort(key=lambda p: -(p["value"] or 0))
    return positions


def summarize(positions: list[dict[str, Any]]) -> dict[str, Any]:
    """Headline numbers, value-weighted where it matters."""
    invested = sum(p["invested"] for p in positions if p["invested"])
    value = sum(p["value"] for p in positions if p["value"])
    gains = [p["gain"] for p in positions if p["gain"] is not None]

    def weighted(key: str) -> float | None:
        pairs = [(p["value"], p[key]) for p in positions
                 if p["value"] and p[key] is not None]
        total = sum(v for v, _ in pairs)
        if not total:
            return None
        return round(sum(v * x for v, x in pairs) / total, 2)

    return {
        "invested": round(invested, 2),
        "value": round(value, 2),
        # Sum of the known P&Ls; None when no position has one.
        "gain": round(sum(gains), 2) if gains else None,
        "weighted_ytm": weighted("ytm"),
        "weighted_years": weighted("years_to_maturity"),
        "issuers": len({p["issuer_name"] for p in positions
                        if p["issuer_name"]}),
        "bonds": len(positions),
    }


def receiving_units(lots: list[dict[str, Any]], flow: Flow, *,
                    known_only: bool = False) -> float:
    """Units across lots that receive ``flow`` — holder semantics: a lot
    gets a flow when it was bought before the record date (or before
    the due date when no record date is published). Lots without a
    purchase date count as held all along, EXCEPT with ``known_only``
    (used for past flows, where "held all along" would fabricate income
    history the user may never have received)."""
    return sum(lot["units"] for lot in lots
               if (lot.get("purchase_date") is not None or not known_only)
               and flow.receivable_by_holder(lot.get("purchase_date")))


def yield_on_cost(lots: list[dict[str, Any]], flows: list[Flow],
                  today: date) -> float | None:
    """Projected effective-annual XIRR: dated purchases and scheduled
    eligible cashflows under the hold-to-maturity scenario. Optional exit
    payouts are excluded; past scheduled dates are not payment evidence.
    None without future flows or when any lot lacks price/date."""
    if not lots or not flows:
        return None
    if any(lot.get("price") is None or lot.get("purchase_date") is None
           for lot in lots):
        return None
    dated: list[tuple[date, float]] = [
        (lot["purchase_date"], -float(lot["units"]) * float(lot["price"]))
        for lot in lots]
    has_future = False
    for f in flows:
        units = receiving_units(lots, f, known_only=True)
        if not units:
            continue
        if f.amount is None or not math.isfinite(f.amount) or f.amount < 0:
            return None
        if f.effective_date > today and f.amount:
            has_future = True
        dated.append((f.effective_date, f.amount * units))
    if not has_future:
        return None  # matured position: nothing left to yield
    return yields.xirr(dated)


def next_payment(lots: list[dict[str, Any]], flows: list[Flow],
                 today: date) -> dict[str, Any] | None:
    """The next future flow at least one lot receives, scaled to the
    receiving units. Holder semantics: a passed record date does not
    hide a coupon the holder is still owed."""
    for f in flows:
        if not f.amount or f.effective_date <= today:
            continue
        units = receiving_units(lots, f)
        if units:
            return {
                "event": f.event, "due_date": f.due_date,
                "record_date": f.record_date,
                "payment_date": f.payment_date,
                "amount": round(f.amount * units, 2),
                "estimated": f.estimated, "future": True,
            }
    return None


def monthly_cashflows(lots_by_isin: dict[str, list[dict[str, Any]]],
                      flows_by_isin: dict[str, list[Flow]],
                      today: date,
                      labels_by_isin: dict[str, str] | None = None,
                      ) -> list[dict[str, Any]]:
    """Past AND future flows x receiving units, bucketed per calendar
    month, with a per-bond breakdown inside each bucket (earliest
    payment first, larger amount breaking ties). ``interest``/``principal``
    are month totals; the past
    portion is broken out in ``received_interest``/``received_principal``
    (split per flow, so a month straddling today is exact).

    Holder semantics, record-date accurate per lot: a flow counts for
    the units bought before its record date — a passed record date
    does not hide money the holder is still owed, but lots bought after
    it don't inflate the month either. Past flows count only lots with
    a KNOWN purchase date: "held all along" is a fine default for
    projections but would fabricate received income.

    Everything keys off the EFFECTIVE date (business-day-adjusted
    payment date, falling back to the due date) — the month bucket, the
    received split and the per-bond dates all match the bank statement,
    even when the adjustment crosses a month boundary.
    """
    labels = labels_by_isin or {}
    buckets: dict[str, dict[str, Any]] = {}
    for isin, flows in flows_by_isin.items():
        lots = lots_by_isin.get(isin) or []
        if not lots:
            continue
        for f in flows:
            if not f.amount:
                continue
            past = f.effective_date <= today
            units = receiving_units(lots, f, known_only=past)
            if not units:
                continue
            month = f.effective_date.strftime("%Y-%m")
            b = buckets.setdefault(
                month, {"month": month, "interest": 0.0, "principal": 0.0,
                        "received_interest": 0.0, "received_principal": 0.0,
                        "estimated": False, "_bonds": {}})
            key = "interest" if f.event == "interest" else "principal"
            b[key] += f.amount * units
            if past:
                b[f"received_{key}"] += f.amount * units
            b["estimated"] = b["estimated"] or f.estimated
            bond = b["_bonds"].setdefault(
                isin, {"isin": isin, "label": labels.get(isin, isin),
                       "interest": 0.0, "principal": 0.0, "estimated": False,
                       "_dates": set()})
            bond[key] += f.amount * units
            bond["estimated"] = bond["estimated"] or f.estimated
            bond["_dates"].add(f.effective_date.isoformat())
    out = sorted(buckets.values(), key=lambda b: b["month"])
    for b in out:
        b["interest"] = round(b["interest"], 2)
        b["principal"] = round(b["principal"], 2)
        b["received_interest"] = round(b["received_interest"], 2)
        b["received_principal"] = round(b["received_principal"], 2)
        bonds = sorted(
            b.pop("_bonds").values(),
            key=lambda x: (min(x["_dates"]),
                           -(x["interest"] + x["principal"])))
        for bond in bonds:
            bond["interest"] = round(bond["interest"], 2)
            bond["principal"] = round(bond["principal"], 2)
            bond["dates"] = sorted(bond.pop("_dates"))
        b["bonds"] = bonds
    return out


def income_summary(months: list[dict[str, Any]]) -> dict[str, float]:
    """Received vs projected totals across the month buckets. Derived
    from the buckets themselves so the banner can never disagree with
    the chart."""
    ri = sum(m["received_interest"] for m in months)
    rp = sum(m["received_principal"] for m in months)
    return {
        "received_interest": round(ri, 2),
        "received_principal": round(rp, 2),
        "projected_interest": round(sum(m["interest"] for m in months) - ri, 2),
        "projected_principal": round(sum(m["principal"] for m in months) - rp, 2),
    }
