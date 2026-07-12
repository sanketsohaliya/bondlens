"""BondLens API — read-side over the scheduled data platform.

No fetch endpoints: the jobs container keeps the database current
(universe daily, NSDL daily, EOD trades nightly) and precomputes the
screener analytics into bond_analytics (scripts/compute_analytics.py).
The API is stateless: the screener is a filtered SELECT; only the
per-bond detail view computes yields live (it supports what-if pricing).
"""
import re
from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Query

from . import db, kite, models, queries
from .analytics import ratings, schedule, yields
from .analytics.compute import STALE_AFTER_DAYS

app = FastAPI(title="BondLens", version="2.0")


def _years(maturity: date | None, today: date) -> float | None:
    if maturity is None or maturity <= today:
        return None
    return round((maturity - today).days / 365.0, 2)


# --------------------------------------------------------------------------
# Screener
# --------------------------------------------------------------------------

@app.get("/api/bonds", response_model=models.BondsResponse)
def bonds(
    min_ytm: float | None = Query(None),
    max_ytm: float | None = Query(None),
    rating: str | None = Query(None, description="Substring match, e.g. AA"),
    min_rating: str | None = Query(None, description="Quality floor, e.g. AA-"),
    sector: str | None = Query(None),
    industry: str | None = Query(None),
    issuer: str | None = Query(None, description="Issuer name substring"),
    exchange: str | None = Query(None, description="NSE or BSE"),
    frequency: str | None = Query(None, description="monthly|quarterly|semiannual|annual"),
    min_years: float | None = Query(None),
    max_years: float | None = Query(None),
    max_ltp: float | None = Query(None, description="Max price per unit"),
    min_turnover: float | None = Query(None, description="Min ₹ turnover on last trade day"),
    secured_only: bool = Query(False),
    tax_free_only: bool = Query(False),
    below_par: bool = Query(False),
    fresh_only: bool = Query(False, description="Exclude stale retail prices"),
    exact_only: bool = Query(False, description="Only exact-schedule YTMs"),
    downgraded_only: bool = Query(False),
    exclude_defaulted: bool = Query(True),
    watchlist_only: bool = Query(False),
    q: str | None = Query(None, description="Search ISIN / symbol / issuer"),
    limit: int = Query(500, le=5000),
    offset: int = Query(0, ge=0),
):
    floor = None
    if min_rating is not None:
        floor = ratings.RATING_RANK.get(min_rating.strip().upper())
        if floor is None:  # unknown grade string matches nothing
            return {"count": 0, "bonds": [], "as_of": None}
    rows, total, as_of = queries.screener({
        "min_ytm": min_ytm, "max_ytm": max_ytm, "rating": rating,
        "min_rating_rank": floor, "sector": sector, "industry": industry,
        "issuer": issuer, "exchange": exchange, "frequency": frequency,
        "min_years": min_years, "max_years": max_years, "max_ltp": max_ltp,
        "min_turnover": min_turnover, "secured_only": secured_only,
        "tax_free_only": tax_free_only, "below_par": below_par,
        "fresh_only": fresh_only, "exact_only": exact_only,
        "downgraded_only": downgraded_only,
        "exclude_defaulted": exclude_defaulted,
        "watchlist_only": watchlist_only, "q": q,
        "limit": limit, "offset": offset,
    }, STALE_AFTER_DAYS)
    return {"count": total, "bonds": rows, "as_of": as_of}


@app.get("/api/filters", response_model=models.Filters)
def filters():
    """Distinct values for filter dropdowns."""
    return {**queries.screener_filters(), "ratings": list(ratings.RATING_ORDER)}


# --------------------------------------------------------------------------
# Bond detail
# --------------------------------------------------------------------------

@app.get("/api/bonds/{isin}", response_model=models.BondDetail)
def bond_detail(isin: str, price: float | None = Query(None, gt=0)):
    """Everything known about one bond. ``price`` reprices every
    price-derived figure for what-if analysis."""
    isin = isin.strip().upper()
    row = queries.bond_row(isin)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown ISIN")
    today = date.today()
    terms = queries.bond_terms(isin)
    raw_flows = queries.bond_cashflows(isin)
    sched = schedule.build(isin, raw_flows, terms, today=today)
    hist = queries.rating_history(isin).get(isin, [])
    cur = [h for h in hist if h.get("is_current")]
    extras = queries.bond_extras(isin)

    core = queries.analytics_row(isin, STALE_AFTER_DAYS) or {}
    custom_price = price is not None
    ltp = price if custom_price else core.get("ltp")
    coupon = core.get("coupon") or (
        float(terms["coupon_rate"]) if terms and terms.get("coupon_rate") else None)
    face = core.get("face_value") or (
        float(terms["face_value"]) if terms and terms.get("face_value") else None)

    exact = yields.exact_ytm(ltp, sched, today) if ltp else None
    approx = yields.approx_ytm(ltp, coupon, face,
                               terms.get("redemption_date") if terms else None,
                               sched.periods_per_year or 1, today) if ltp else None
    best = exact if exact is not None else approx
    accrued = yields.accrued_interest(sched, today)
    call_dates = _parse_call_dates(terms)
    ytw = (yields.yield_to_worst(ltp, sched, call_dates, today)
           if ltp and call_dates else None)

    flows_out = []
    future_total = 0.0
    for f in sched.flows:
        fut = f.receivable_by_buyer(today)
        if fut and f.amount:
            future_total += f.amount
        flows_out.append({
            "event": f.event, "due_date": f.due_date,
            "record_date": f.record_date, "amount": f.amount,
            "estimated": f.estimated, "future": fut,
        })
    next_payment = next((f for f in flows_out if f["future"]), None)

    return {
        **row,
        "terms": terms,
        "kite_url": kite.kite_url(row.get("nse_symbol"), row.get("nse_series")),
        "ltp": ltp,
        "custom_price": custom_price,
        "trade_date": core.get("trade_date"),
        "days_since_traded": core.get("days_since_traded"),
        "otc_price_pct": core.get("otc_price_pct"),
        "otc_yield": core.get("otc_yield"),
        "otc_trade_date": core.get("otc_trade_date"),
        "coupon": coupon,
        "face_value": face,
        "frequency": sched.frequency,
        "schedule_shape": sched.shape,
        "schedule_usable": sched.usable,
        "schedule_warnings": sched.warnings,
        "amounts_estimated": sched.amounts_estimated,
        "maturity_date": terms.get("redemption_date") if terms else None,
        "exchange": core.get("exchange"),
        "years_to_maturity": _years(
            terms.get("redemption_date") if terms else None, today),
        "ytm_exact": exact,
        "ytm_approx": approx,
        "ytm": best,
        "yield_to_worst": ytw,
        "call_dates": call_dates,
        "current_yield": yields.current_yield(ltp, coupon, face),
        "accrued": accrued,
        "clean_price": yields.clean_price(ltp, sched, today) if ltp else None,
        "duration": yields.modified_duration(ltp, sched, best, today),
        "credit_rating": ratings.composite(cur),
        "rating_rank": ratings.best_rank(cur),
        "in_default": ratings.in_default(cur) or bool(extras["defaults"])
                      or bool(extras["technical_defaults"]),
        "rating_timeline": ratings.timeline(hist),
        "cashflows": flows_out,
        "future_total_per_unit": round(future_total, 2),
        "next_payment": next_payment,
        **extras,
        "issuer_bonds": queries.issuer_siblings(isin, row["issuer_name"])
                        if row.get("issuer_name") else [],
        "in_watchlist": bool(core.get("in_watchlist")),
    }


def _parse_call_dates(terms: dict | None) -> list[date]:
    """Extract usable call dates from bond_terms' free-text fields."""
    if not terms or (terms.get("call_indicator") or "").lower() != "yes":
        return []
    text = terms.get("call_specified_dates") or terms.get("call_deadline") or ""
    dates = []
    for m in re.finditer(r"(\d{1,2})[-/](\d{1,2}|\w{3})[-/](\d{4})", str(text)):
        d, mth, y = m.groups()
        try:
            if mth.isdigit():
                dates.append(date(int(y), int(mth), int(d)))
            else:
                dates.append(datetime.strptime(
                    f"{d}-{mth}-{y}", "%d-%b-%Y").date())
        except ValueError:
            continue
    return sorted(set(dates))


# --------------------------------------------------------------------------
# Trade history
# --------------------------------------------------------------------------

@app.get("/api/bonds/{isin}/trades", response_model=models.TradesResponse)
def bond_trades(
    isin: str,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    exchange: str | None = Query(None),
    segment: str | None = Query(None),
):
    rows = queries.trades(isin.strip().upper(), date_from, date_to,
                          exchange, segment)
    return {"isin": isin, "count": len(rows), "trades": rows}


# --------------------------------------------------------------------------
# Watchlist
# --------------------------------------------------------------------------

@app.get("/api/watchlist", response_model=models.WatchlistResponse)
def watchlist():
    return {"watchlist": db.get_watchlist()}


@app.post("/api/watchlist/{isin}", response_model=models.WatchToggle)
def watch(isin: str):
    isin = isin.strip().upper()
    try:
        db.add_to_watchlist(isin)
    except Exception:  # noqa: BLE001 - FK violation = unknown ISIN
        raise HTTPException(status_code=404, detail="Unknown ISIN") from None
    return {"isin": isin, "watched": True}


@app.delete("/api/watchlist/{isin}", response_model=models.WatchToggle)
def unwatch(isin: str):
    isin = isin.strip().upper()
    db.remove_from_watchlist(isin)
    return {"isin": isin, "watched": False}


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------

@app.get("/api/status", response_model=models.Status)
def status():
    return queries.status()


@app.get("/", response_model=models.Health)
def health():
    return {"app": "BondLens", "version": "2.0"}
