"""BondLens API — read-side over the scheduled data platform.

No fetch endpoints: the jobs container keeps the database current
(universe daily, NSDL daily, EOD trades nightly) and precomputes the
explorer analytics into bond_analytics (scripts/compute_analytics.py).
The API is stateless: the explorer is a filtered SELECT; only the
per-bond detail view computes yields live (it supports what-if pricing).
"""
from datetime import date

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Response

from . import auth, db, depth, kite, models, queries
from .analytics import portfolio as pf
from .analytics import ratings, schedule, yields
from .analytics.compute import STALE_AFTER_DAYS

# redirect_slashes builds its 307 from the proxied (http) scheme, which
# bounces through Caddy's cacheable http->https 308 and can poison a
# browser's redirect cache into a loop. No API URL has a trailing slash,
# so a plain 404 is safer than a redirect.
app = FastAPI(title="BondLens", version="2.0", redirect_slashes=False)

# Everything except auth and the health check requires a session.
router = APIRouter(dependencies=[Depends(auth.require_user)])


def _years(maturity: date | None, today: date) -> float | None:
    if maturity is None or maturity <= today:
        return None
    return round((maturity - today).days / 365.0, 2)


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

@app.post("/api/auth/google", response_model=models.User)
def auth_google(response: Response, credential: str = Body(embed=True)):
    """Verify a Google ID token (from GIS button / One Tap), enforce the
    allowlist, upsert the user and set the session cookie."""
    claims = auth.verify_google_token(credential)
    user = db.upsert_user(claims["sub"], claims["email"],
                          claims.get("name"), claims.get("picture"))
    response.set_cookie(
        auth.SESSION_COOKIE, auth.mint_session(user["id"]),
        max_age=auth.SESSION_DAYS * 86400, httponly=True,
        samesite="lax", secure=auth.COOKIE_SECURE,
    )
    return user


@app.get("/api/auth/me", response_model=models.User)
def auth_me(user_id: int = Depends(auth.require_user)):
    user = db.get_user(user_id)
    if user is None:  # user row deleted; session is stale
        raise HTTPException(status_code=401, detail="Sign in required")
    return user


@app.post("/api/auth/logout", response_model=models.Ok)
def auth_logout(response: Response):
    response.delete_cookie(auth.SESSION_COOKIE)
    return {"ok": True}


# --------------------------------------------------------------------------
# Per-user lists (named watchlists; holdings mirror the portfolio)
# --------------------------------------------------------------------------

@router.get("/api/lists", response_model=models.ListsResponse)
def lists(user_id: int = Depends(auth.require_user)):
    return db.get_user_lists(user_id)


def _watchlist_call(fn, *args):
    """Run a db watchlist helper, mapping WatchlistError to HTTP."""
    try:
        return fn(*args)
    except db.WatchlistError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e


@router.post("/api/watchlists", response_model=models.Watchlist)
def watchlist_create(body: models.WatchlistName,
                     user_id: int = Depends(auth.require_user)):
    return _watchlist_call(db.create_watchlist, user_id, body.name)


@router.patch("/api/watchlists/{watchlist_id}", response_model=models.Ok)
def watchlist_rename(watchlist_id: int, body: models.WatchlistName,
                     user_id: int = Depends(auth.require_user)):
    _watchlist_call(db.rename_watchlist, user_id, watchlist_id, body.name)
    return {"ok": True}


@router.delete("/api/watchlists/{watchlist_id}", response_model=models.Ok)
def watchlist_delete(watchlist_id: int,
                     user_id: int = Depends(auth.require_user)):
    _watchlist_call(db.delete_watchlist, user_id, watchlist_id)
    return {"ok": True}


@router.put("/api/watchlists/{watchlist_id}/{isin}",
            response_model=models.WatchlistToggle)
def watchlist_add_isin(watchlist_id: int, isin: str,
                       user_id: int = Depends(auth.require_user)):
    _watchlist_call(db.add_watchlist_item, user_id, watchlist_id,
                    isin.strip().upper())
    return {"watchlist_id": watchlist_id, "isin": isin, "present": True}


@router.delete("/api/watchlists/{watchlist_id}/{isin}",
               response_model=models.WatchlistToggle)
def watchlist_remove_isin(watchlist_id: int, isin: str,
                          user_id: int = Depends(auth.require_user)):
    _watchlist_call(db.remove_watchlist_item, user_id, watchlist_id,
                    isin.strip().upper())
    return {"watchlist_id": watchlist_id, "isin": isin, "present": False}


# --------------------------------------------------------------------------
# Portfolio
# --------------------------------------------------------------------------

@router.post("/api/portfolio/lots", response_model=models.PortfolioLot)
def lot_add(body: models.LotCreate,
            user_id: int = Depends(auth.require_user)):
    return _watchlist_call(db.add_lot, user_id, body.isin.strip().upper(),
                           body.units, body.price, body.purchase_date,
                           body.platform)


@router.patch("/api/portfolio/lots/{lot_id}", response_model=models.PortfolioLot)
def lot_update(lot_id: int, body: models.LotUpdate,
               user_id: int = Depends(auth.require_user)):
    return _watchlist_call(db.update_lot, user_id, lot_id, body.units,
                           body.price, body.purchase_date, body.platform)


@router.delete("/api/portfolio/lots/{lot_id}", response_model=models.Ok)
def lot_delete(lot_id: int, user_id: int = Depends(auth.require_user)):
    _watchlist_call(db.delete_lot, user_id, lot_id)
    return {"ok": True}


@router.get("/api/portfolio", response_model=models.PortfolioResponse)
def portfolio(user_id: int = Depends(auth.require_user)):
    """Everything the portfolio page needs in one response: positions
    (lots x precomputed analytics), headline summary, monthly future
    cashflows (record-date accurate) and recent rating events as news."""
    lots = db.get_portfolio_lots(user_id)
    isins = sorted({lot["isin"] for lot in lots})
    today = date.today()

    core_by_isin: dict[str, dict] = {}
    flows_by_isin: dict[str, list[schedule.Flow]] = {}
    for isin in isins:  # a portfolio holds few ISINs; per-ISIN reads are fine
        core_by_isin[isin] = queries.analytics_row(isin, STALE_AFTER_DAYS) or {}
        sched = schedule.build(isin, queries.bond_cashflows(isin),
                               queries.bond_terms(isin), today=today)
        flows_by_isin[isin] = sched.flows

    positions = pf.build_positions(lots, core_by_isin)
    for p in positions:
        nxt = next((f for f in flows_by_isin[p["isin"]]
                    if f.amount and f.receivable_by_buyer(today)), None)
        p["next_payment"] = None if nxt is None else {
            "event": nxt.event, "due_date": nxt.due_date,
            "record_date": nxt.record_date,
            "amount": round(nxt.amount * p["units"], 2) if nxt.amount else None,
            "estimated": nxt.estimated, "future": True,
        }

    return {
        "summary": pf.summarize(positions),
        "positions": positions,
        "cashflows": pf.monthly_cashflows(
            {p["isin"]: p["units"] for p in positions}, flows_by_isin, today),
        "news": queries.ratings_for_isins(isins),
    }


# --------------------------------------------------------------------------
# Explorer
# --------------------------------------------------------------------------

@router.get("/api/bonds", response_model=models.BondsResponse)
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
    payout: str | None = Query(
        None, pattern="^(maturity|periodic)$",
        description="maturity = zero-coupon/cumulative, periodic = coupon stream"),
    min_years: float | None = Query(None),
    max_years: float | None = Query(None),
    max_ltp: float | None = Query(None, description="Max price per unit"),
    min_turnover: float | None = Query(
        None, description="Min ₹ retail turnover on last trade day"),
    min_otc_turnover: float | None = Query(
        None, description="Min ₹ OTC turnover on last OTC trade day"),
    retail_only: bool = Query(False, description="Only bonds with retail (exchange) trades"),
    otc_only: bool = Query(False, description="Only bonds with OTC trades"),
    secured_only: bool = Query(False),
    unsecured_only: bool = Query(False),
    tax_free_only: bool = Query(False),
    below_par: bool = Query(False),
    fresh_only: bool = Query(False, description="Exclude stale retail prices"),
    traded_within: int | None = Query(
        None, ge=1, description="Last retail trade at most N days ago"),
    exact_only: bool = Query(False, description="Only exact-schedule YTMs"),
    downgraded_only: bool = Query(False),
    isins: str | None = Query(
        None, description="Comma-separated ISIN list "
                          "(e.g. watchlist/portfolio contents)"),
    q: str | None = Query(None, description="Search ISIN / symbol / issuer"),
    limit: int = Query(500, le=5000),
    offset: int = Query(0, ge=0),
):
    floor = None
    if min_rating is not None:
        floor = ratings.RATING_RANK.get(min_rating.strip().upper())
        if floor is None:  # unknown grade string matches nothing
            return {"count": 0, "bonds": [], "as_of": None}
    rows, total, as_of = queries.explorer({
        "min_ytm": min_ytm, "max_ytm": max_ytm, "rating": rating,
        "min_rating_rank": floor, "sector": sector, "industry": industry,
        "issuer": issuer, "exchange": exchange, "frequency": frequency,
        "payout": payout,
        "min_years": min_years, "max_years": max_years, "max_ltp": max_ltp,
        "min_turnover": min_turnover, "min_otc_turnover": min_otc_turnover,
        "retail_only": retail_only, "otc_only": otc_only,
        "secured_only": secured_only, "unsecured_only": unsecured_only,
        "tax_free_only": tax_free_only, "below_par": below_par,
        "fresh_only": fresh_only, "traded_within": traded_within,
        "exact_only": exact_only,
        "downgraded_only": downgraded_only,
        "isins": [s.strip().upper() for s in isins.split(",") if s.strip()]
                 if isins else None,
        "q": q,
        "limit": limit, "offset": offset,
    }, STALE_AFTER_DAYS)
    return {"count": total, "bonds": rows, "as_of": as_of}


@router.get("/api/filters", response_model=models.Filters)
def filters():
    """Distinct values for filter dropdowns."""
    return {**queries.explorer_filters(), "ratings": list(ratings.RATING_ORDER)}


# --------------------------------------------------------------------------
# Bond detail
# --------------------------------------------------------------------------

@router.get("/api/bonds/{isin}", response_model=models.BondDetail)
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

    red_date = terms.get("redemption_date") if terms else None
    perpetual = bool(terms and terms.get("perpetual")) or (
        red_date is not None and red_date.year >= 2500)
    if perpetual:
        red_date = None
    call_dates = yields.parse_call_dates(terms)
    if perpetual:
        # A maturity-based YTM against a perpetual (9999 sentinel) is
        # spurious — yield-to-call is the meaningful figure.
        exact = approx = None
        best = ytw = (yields.yield_to_call(ltp, sched, call_dates, today)
                      if ltp else None)
    else:
        exact = yields.exact_ytm(ltp, sched, today) if ltp else None
        approx = yields.approx_ytm(ltp, coupon, face, red_date,
                                   sched.periods_per_year or 1,
                                   today) if ltp else None
        best = exact if exact is not None else approx
        ytw = (yields.yield_to_worst(ltp, sched, call_dates, today)
               if ltp and call_dates else None)
    accrued = yields.accrued_interest(sched, today)

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
        "trustee_name": queries.bond_trustee(isin),
        "kite_url": kite.kite_url(row.get("nse_symbol"), row.get("nse_series"),
                                  row.get("bse_scrip_id"),
                                  prefer=core.get("exchange")),
        "ltp": ltp,
        "custom_price": custom_price,
        "trade_date": core.get("trade_date"),
        "days_since_traded": core.get("days_since_traded"),
        "otc_price_pct": core.get("otc_price_pct"),
        "otc_yield": core.get("otc_yield"),
        "otc_trade_date": core.get("otc_trade_date"),
        "coupon": coupon,
        "implied_coupon": (yields.implied_coupon(
            sched, terms.get("issue_price") if terms else None)
            if coupon is None else None),
        "face_value": face,
        "frequency": sched.frequency,
        "schedule_shape": sched.shape,
        "schedule_usable": sched.usable,
        "schedule_warnings": sched.warnings,
        "amounts_estimated": sched.amounts_estimated,
        "issue_date": terms.get("allotment_date") if terms else None,
        "maturity_date": red_date,
        "exchange": core.get("exchange"),
        "years_to_maturity": _years(red_date, today),
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
        "in_default": ratings.in_default(cur) or bool(extras["defaults"]),
        "tech_delay": bool(extras["technical_defaults"]),
        "rating_timeline": ratings.timeline(hist),
        "cashflows": flows_out,
        "future_total_per_unit": round(future_total, 2),
        "next_payment": next_payment,
        **extras,
        "issuer_bonds": queries.issuer_siblings(isin, row["issuer_name"])
                        if row.get("issuer_name") else [],
    }


# --------------------------------------------------------------------------
# Trade history
# --------------------------------------------------------------------------

@router.get("/api/bonds/{isin}/trades", response_model=models.TradesResponse)
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
# Order book
# --------------------------------------------------------------------------

@router.get("/api/bonds/{isin}/depth", response_model=models.OrderBook)
def bond_depth(isin: str):
    """Live top-5 bid/ask depth from both exchanges, fetched on demand
    (30s cache). Ask levels are annotated with the YTM a buyer would
    lock at that dirty price."""
    isin = isin.strip().upper()
    row = queries.bond_row(isin)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown ISIN")
    book = depth.order_book(isin, row.get("nse_symbol"), row.get("nse_series"),
                            row.get("bse_scrip_code"))
    if not book["ytm_at_ask"]:
        ask_prices = sorted({
            lv["price"]
            for side in (book.get("nse"), book.get("bse")) if side
            for lv in side["asks"] if lv["price"]
        })
        if ask_prices:
            today = date.today()
            sched = schedule.build(isin, queries.bond_cashflows(isin),
                                   queries.bond_terms(isin), today=today)
            if sched.usable:
                book["ytm_at_ask"] = [
                    {"price": p, "ytm": yields.exact_ytm(p, sched, today)}
                    for p in ask_prices
                ]
    return book


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------

@router.get("/api/status", response_model=models.Status)
def status():
    return queries.status()


@app.get("/", response_model=models.Health)
def health():
    return {"app": "BondLens", "version": "2.0"}


app.include_router(router)
