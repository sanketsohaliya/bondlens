"""BondLens API — read-side over the scheduled data platform.

No fetch endpoints: the jobs container keeps the database current
(universe daily, NSDL daily, EOD trades nightly). The API computes
analytics (schedules, yields, ratings) on top and serves the screener,
bond detail, trade history, watchlist and data-freshness status.

The screener core (universe x terms x ratings x latest trades x schedule
analytics) is rebuilt at most every CORE_TTL_SECONDS and cached — the
underlying data only changes when a job runs.
"""
import re
import threading
import time
from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Query

from . import db, kite, queries
from .analytics import ratings, schedule, yields

app = FastAPI(title="BondLens", version="2.0")

CORE_TTL_SECONDS = 600
STALE_AFTER_DAYS = 7   # retail price older than this is flagged stale

_core_lock = threading.Lock()
_core: dict = {"built_at": 0.0, "bonds": []}


# --------------------------------------------------------------------------
# Screener core
# --------------------------------------------------------------------------

def _build_core() -> list[dict]:
    """Join everything once and compute per-bond analytics."""
    today = date.today()
    core = queries.screener_core()
    cashflows = queries.all_cashflows()
    terms_map = queries.all_terms()
    current = queries.current_ratings()
    history = queries.rating_history()
    defaulted = queries.defaulted_isins()
    watch = {w["isin"] for w in db.get_watchlist()}

    out = []
    for isin, r in core.items():
        sched = schedule.build(isin, cashflows.get(isin, []),
                               terms_map.get(isin), today=today)
        cur = current.get(isin, [])
        hist = history.get(isin, [])
        retail = r.get("retail_trade")
        otc = r.get("otc_trade")

        price = retail and (retail.get("close") or retail.get("last_price"))
        price = float(price) if price else None
        coupon = float(r["coupon_rate"]) if r.get("coupon_rate") else None
        face = float(r["face_value"]) if r.get("face_value") else None

        ytm = ytm_method = None
        if price:
            exact = yields.exact_ytm(price, sched, today)
            if exact is not None:
                ytm, ytm_method = exact, (
                    "estimated" if sched.amounts_estimated else "exact")
            else:
                approx = yields.approx_ytm(
                    price, coupon, face, r.get("redemption_date"),
                    sched.periods_per_year or 1, today)
                if approx is not None:
                    ytm, ytm_method = approx, "approx"

        change = ratings.recent_change(hist, today)
        days_since = ((today - retail["trade_date"]).days if retail else None)
        otc_days_since = ((today - otc["trade_date"]).days if otc else None)

        out.append({
            "isin": isin,
            "issuer_name": r.get("issuer_name"),
            "sector": r.get("sector"),
            "industry": r.get("industry"),
            "on_nse": r["on_nse"], "on_bse": r["on_bse"],
            "nse_symbol": r.get("nse_symbol"), "nse_series": r.get("nse_series"),
            "bse_scrip_id": r.get("bse_scrip_id"),
            "coupon": coupon,
            "face_value": face,
            "coupon_basis": r.get("coupon_basis"),
            "frequency": sched.frequency,
            "secured": _yn(r.get("secured")),
            "tax_free": _yn(r.get("tax_free")),
            "perpetual": bool(r.get("perpetual")),
            "maturity_date": r.get("redemption_date"),
            "years_to_maturity": _years(r.get("redemption_date"), today),
            "schedule_shape": sched.shape,
            "ltp": price,
            "trade_date": retail["trade_date"] if retail else None,
            "exchange": retail["exchange"] if retail else None,
            "volume": _f(retail and retail.get("volume")),
            "turnover": _f(retail and retail.get("turnover")),
            "num_trades": retail.get("num_trades") if retail else None,
            "days_since_traded": days_since,
            "stale": days_since is None or days_since > STALE_AFTER_DAYS,
            "otc_price_pct": _f(otc and (otc.get("wap") or otc.get("last_price"))),
            "otc_yield": _f(otc and (otc.get("wap_yield") or otc.get("last_yield"))),
            "otc_trade_date": otc["trade_date"] if otc else None,
            "otc_days_since_traded": otc_days_since,
            "ytm": ytm,
            "ytm_method": ytm_method,
            "current_yield": yields.current_yield(price, coupon, face),
            "duration": yields.modified_duration(price, sched, ytm, today)
                        if ytm_method in ("exact", "estimated") else None,
            "credit_rating": ratings.composite(cur),
            "rating_rank": ratings.best_rank(cur),
            "rating_change": change["direction"] if change else None,
            "rating_prev": change["prev_rating"] if change else None,
            "rating_changed_at": change["rating_date"] if change else None,
            "in_default": isin in defaulted or ratings.in_default(cur),
            "in_watchlist": isin in watch,
        })
    out.sort(key=lambda b: b["turnover"] or 0, reverse=True)
    return out


def _get_core(force: bool = False) -> list[dict]:
    with _core_lock:
        if force or time.time() - _core["built_at"] > CORE_TTL_SECONDS:
            _core["bonds"] = _build_core()
            _core["built_at"] = time.time()
        return _core["bonds"]


def _yn(v: str | None) -> bool | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    return True if s in ("yes", "true", "t", "y") else False if s in ("no", "false", "f", "n") else None


def _f(v) -> float | None:
    return float(v) if v is not None else None


def _years(maturity: date | None, today: date) -> float | None:
    if maturity is None or maturity <= today:
        return None
    return round((maturity - today).days / 365.0, 2)


# --------------------------------------------------------------------------
# Screener
# --------------------------------------------------------------------------

@app.get("/api/bonds")
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
    rows = _get_core()
    floor = ratings.RATING_RANK.get((min_rating or "").strip().upper())
    needle = (q or "").strip().upper()
    out = []
    for r in rows:
        if watchlist_only and not r["in_watchlist"]:
            continue
        if exclude_defaulted and r["in_default"] and not watchlist_only:
            continue
        if min_ytm is not None and (r["ytm"] is None or r["ytm"] < min_ytm):
            continue
        if max_ytm is not None and (r["ytm"] is None or r["ytm"] > max_ytm):
            continue
        if rating and rating.upper() not in (r["credit_rating"] or "").upper():
            continue
        if min_rating is not None and (
                floor is None or r["rating_rank"] is None
                or r["rating_rank"] > floor):
            continue
        if sector and sector.lower() not in (r["sector"] or "").lower():
            continue
        if industry and industry.lower() not in (r["industry"] or "").lower():
            continue
        if issuer and issuer.lower() not in (r["issuer_name"] or "").lower():
            continue
        if exchange:
            if exchange.upper() == "NSE" and not r["on_nse"]:
                continue
            if exchange.upper() == "BSE" and not r["on_bse"]:
                continue
        if frequency and r["frequency"] != frequency.lower():
            continue
        if min_years is not None and (r["years_to_maturity"] is None
                                      or r["years_to_maturity"] < min_years):
            continue
        if max_years is not None and (r["years_to_maturity"] is None
                                      or r["years_to_maturity"] > max_years):
            continue
        if max_ltp is not None and (r["ltp"] is None or r["ltp"] > max_ltp):
            continue
        if min_turnover is not None and (r["turnover"] is None
                                         or r["turnover"] < min_turnover):
            continue
        if secured_only and r["secured"] is not True:
            continue
        if tax_free_only and r["tax_free"] is not True:
            continue
        if below_par and not (r["ltp"] and r["face_value"]
                              and r["ltp"] < r["face_value"]):
            continue
        if fresh_only and r["stale"]:
            continue
        if exact_only and r["ytm_method"] != "exact":
            continue
        if downgraded_only and r["rating_change"] != "down":
            continue
        if needle and needle not in r["isin"] and needle not in (
                (r["nse_symbol"] or "") + " " + (r["issuer_name"] or "")).upper():
            continue
        out.append(r)
    return {"count": len(out), "bonds": out[offset:offset + limit],
            "as_of": _core["built_at"]}


@app.get("/api/filters")
def filters():
    """Distinct values for filter dropdowns."""
    rows = _get_core()
    return {
        "sectors": sorted({r["sector"] for r in rows if r["sector"]}),
        "frequencies": sorted({r["frequency"] for r in rows if r["frequency"]}),
        "ratings": [g for g in ratings.RATING_ORDER],
    }


# --------------------------------------------------------------------------
# Bond detail
# --------------------------------------------------------------------------

@app.get("/api/bonds/{isin}")
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

    core = {b["isin"]: b for b in _get_core()}.get(isin, {})
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
        "in_watchlist": isin in {w["isin"] for w in db.get_watchlist()},
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

@app.get("/api/bonds/{isin}/trades")
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

@app.get("/api/watchlist")
def watchlist():
    return {"watchlist": db.get_watchlist()}


@app.post("/api/watchlist/{isin}")
def watch(isin: str):
    isin = isin.strip().upper()
    try:
        db.add_to_watchlist(isin)
    except Exception:  # noqa: BLE001 - FK violation = unknown ISIN
        raise HTTPException(status_code=404, detail="Unknown ISIN") from None
    _invalidate_watch(isin, True)
    return {"isin": isin, "watched": True}


@app.delete("/api/watchlist/{isin}")
def unwatch(isin: str):
    isin = isin.strip().upper()
    db.remove_from_watchlist(isin)
    _invalidate_watch(isin, False)
    return {"isin": isin, "watched": False}


def _invalidate_watch(isin: str, watched: bool):
    """Patch the cached core in place instead of a full rebuild."""
    with _core_lock:
        for b in _core["bonds"]:
            if b["isin"] == isin:
                b["in_watchlist"] = watched


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------

@app.get("/api/status")
def status():
    return queries.status()


@app.get("/")
def health():
    return {"app": "BondLens", "version": "2.0"}
