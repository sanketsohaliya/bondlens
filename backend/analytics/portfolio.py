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
from datetime import date
from typing import Any

from . import ratings
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
            "issuer_name": core.get("issuer_name"),
            "credit_rating": core.get("credit_rating"),
            "rating_rank": rank,
            "rating_grade": ratings.RATING_ORDER[rank]
                            if rank is not None else None,
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


def monthly_cashflows(units_by_isin: dict[str, float],
                      flows_by_isin: dict[str, list[Flow]],
                      today: date) -> list[dict[str, Any]]:
    """Future receivable flows x units, bucketed per calendar month.

    Record-date accurate: a flow whose record date has passed belongs to
    the seller, so it is excluded even if the due date is ahead.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for isin, flows in flows_by_isin.items():
        units = units_by_isin.get(isin, 0)
        if not units:
            continue
        for f in flows:
            if not f.amount or not f.receivable_by_buyer(today):
                continue
            month = f.due_date.strftime("%Y-%m")
            b = buckets.setdefault(
                month, {"month": month, "interest": 0.0, "principal": 0.0,
                        "estimated": False})
            key = "interest" if f.event == "interest" else "principal"
            b[key] += f.amount * units
            b["estimated"] = b["estimated"] or f.estimated
    out = sorted(buckets.values(), key=lambda b: b["month"])
    for b in out:
        b["interest"] = round(b["interest"], 2)
        b["principal"] = round(b["principal"], 2)
    return out
