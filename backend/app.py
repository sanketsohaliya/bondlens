"""BondLens API — read-side over the scheduled data platform.

No fetch endpoints: the jobs container keeps the database current
(universe daily, NSDL daily, EOD trades nightly) and precomputes the
explorer analytics into bond_analytics (scripts/compute_analytics.py).
The API is stateless: the explorer is a filtered SELECT; only the
per-bond detail view computes yields live (it supports what-if pricing).
"""
import asyncio
import contextlib
from datetime import UTC, date, datetime

from fastapi import (
    APIRouter,
    Body,
    Cookie,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse

from . import auth, db, depth, kite, kite_connect, kite_ticker, models, queries
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

def _cashflow_label(core: dict, isin: str) -> str:
    """Issuer name for cashflow breakdowns (the ISIN column carries
    uniqueness; two bonds of one issuer share a label by design).
    Falls back to the exchange symbol, then the ISIN."""
    return (core.get("issuer_name") or core.get("nse_symbol")
            or core.get("bse_scrip_id") or isin)


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
        p["next_payment"] = pf.next_payment(p["lots"],
                                            flows_by_isin[p["isin"]], today)

    cashflows = pf.monthly_cashflows(
        {p["isin"]: p["lots"] for p in positions}, flows_by_isin, today,
        {isin: _cashflow_label(core, isin)
         for isin, core in core_by_isin.items()})
    return {
        "summary": pf.summarize(positions),
        "positions": positions,
        "cashflows": cashflows,
        "income": pf.income_summary(cashflows),
        "news": queries.ratings_for_isins(isins),
    }


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------

@router.get("/api/notifications", response_model=models.NotificationsResponse)
def notifications(limit: int = Query(50, ge=1, le=200),
                  user_id: int = Depends(auth.require_user)):
    """Newest-first portfolio notification feed with the unread count.
    Rows are produced by the scheduled generator, not this API."""
    return db.get_notifications(user_id, limit)


@router.post("/api/notifications/read", response_model=models.Ok)
def notifications_read(user_id: int = Depends(auth.require_user)):
    """Mark every notification read (the bell panel calls this on open)."""
    db.mark_notifications_read(user_id)
    return {"ok": True}


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
        None, ge=1,
        description="Last retail trade within the last N trading sessions"),
    exact_only: bool = Query(False, description="Only exact-schedule YTMs"),
    downgraded_only: bool = Query(False),
    isins: str | None = Query(
        None, description="Comma-separated ISIN list "
                          "(e.g. watchlist/portfolio contents)"),
    q: str | None = Query(None, description="Search ISIN / symbol / issuer"),
    sort: str | None = Query(
        None, description="Page ordering: ytm, ltp, turnover, rating_rank, "
                          "years_to_maturity or days_since_traded"),
    sort_dir: str = Query("desc", alias="dir", pattern="^(asc|desc)$"),
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
        "sort": sort, "dir": sort_dir,
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

def _annotate_ytm(book: dict, isin: str) -> None:
    """Fill book['ytm_at_ask'] with the YTM a buyer would lock at each
    resting ask price (dirty)."""
    if book["ytm_at_ask"]:
        return
    ask_prices = sorted({
        lv["price"]
        for side in (book.get("nse"), book.get("bse")) if side
        for lv in side["asks"] if lv["price"]
    })
    if not ask_prices:
        return
    today = date.today()
    sched = schedule.build(isin, queries.bond_cashflows(isin),
                           queries.bond_terms(isin), today=today)
    if sched.usable:
        book["ytm_at_ask"] = [
            {"price": p, "ytm": yields.exact_ytm(p, sched, today)}
            for p in ask_prices
        ]


@router.get("/api/bonds/{isin}/depth", response_model=models.OrderBook)
def bond_depth(isin: str, user_id: int = Depends(auth.require_user)):
    """Live top-5 bid/ask depth from both exchanges — via the user's
    own Kite session when connected, exchange scrapers otherwise —
    fetched on demand (30s cache). Ask levels are annotated with the
    YTM a buyer would lock at that dirty price."""
    isin = isin.strip().upper()
    row = queries.bond_row(isin)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown ISIN")
    book = depth.order_book(isin, row.get("nse_symbol"), row.get("nse_series"),
                            row.get("bse_scrip_code"),
                            bse_scrip_id=row.get("bse_scrip_id"),
                            user_id=user_id)
    _annotate_ytm(book, isin)
    return book


# --------------------------------------------------------------------------
# Kite Connect (per-user Zerodha sessions, live depth, orders)
# --------------------------------------------------------------------------

def _kite_call(fn, *args):
    """Run a kite_connect helper, mapping KiteError to HTTP."""
    try:
        return fn(*args)
    except kite_connect.KiteError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e


@router.get("/api/kite/status", response_model=models.KiteStatus)
def kite_status(request: Request, user_id: int = Depends(auth.require_user)):
    """Kite Connect state for the signed-in user. The UI opens
    login_url to (re)connect; tokens expire every morning.

    login_url carries a short-lived state token + this request's origin
    (via Kite's redirect_params): the app serves multiple domains but
    Kite allows one redirect URL, so the callback needs both to
    identify the user and send them back where they started."""
    origin = None
    if host := request.headers.get("host"):
        proto = request.headers.get("x-forwarded-proto",
                                    "https" if auth.COOKIE_SECURE else "http")
        origin = f"{proto}://{host}"
    return {
        "configured": kite_connect.configured(),
        "connected": kite_connect.token_for(user_id) is not None,
        "login_url": kite_connect.login_url(
            state=auth.mint_kite_state(user_id) if auth.SESSION_SECRET else None,
            origin=origin,
        ),
    }


# On `app`, not `router`: the redirect may land on a different domain
# than the user's session cookie (one Kite redirect URL, two domains),
# so auth comes from the state token when the cookie is absent.
@app.get("/api/kite/callback")
def kite_callback(request_token: str | None = Query(None),
                  status: str | None = Query(None),
                  state: str | None = Query(None),
                  origin: str | None = Query(None),
                  bl_session: str | None = Cookie(default=None)):
    """Kite login redirect target (register {domain-A}/api/kite/callback
    as the app's redirect URL). Stores the daily token for whichever
    BondLens user started the login, then sends them back to the
    (allowlisted) domain they came from."""
    dest = kite_connect.safe_origin(origin)
    user_id = (auth.parse_session(bl_session or "")
               or auth.parse_kite_state(state or ""))
    if user_id is not None and status != "error" and request_token:
        try:
            kite_connect.exchange_token(user_id, request_token)
            return RedirectResponse(f"{dest}/?kite=connected")
        except kite_connect.KiteError:
            pass
    return RedirectResponse(f"{dest}/?kite=error")


@router.post("/api/kite/disconnect", response_model=models.Ok)
def kite_disconnect(user_id: int = Depends(auth.require_user)):
    kite_connect.clear_token(user_id)
    return {"ok": True}


def _order_symbol(isin: str, exchange: str) -> str:
    """Kite tradingsymbol for a bond's listing on `exchange` (404/409
    when the bond or its Kite listing is unknown)."""
    row = queries.bond_row(isin)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown ISIN")
    hit = (kite.resolve_nse(row.get("nse_symbol"), row.get("nse_series"))
           if exchange == "NSE" else kite.resolve_bse(row.get("bse_scrip_id")))
    if hit is None:
        raise HTTPException(status_code=409,
                            detail=f"Bond not tradable on Kite ({exchange})")
    return hit[0]


@router.post("/api/kite/orders", response_model=models.KiteOrderId)
def kite_place_order(body: models.KiteOrderRequest,
                     user_id: int = Depends(auth.require_user)):
    """Place a LIMIT/CNC/DAY order on the user's own Zerodha account.
    Zerodha's RMS is the final gate; its rejection reasons pass through."""
    tradingsymbol = _order_symbol(body.isin.strip().upper(), body.exchange)
    order_id = _kite_call(kite_connect.place_order, user_id, body.exchange,
                          tradingsymbol, body.transaction_type,
                          body.quantity, body.price)
    return {"order_id": order_id}


@router.get("/api/kite/orders", response_model=models.KiteOrdersResponse)
def kite_orders(user_id: int = Depends(auth.require_user)):
    """Today's orders on the user's Zerodha account (Kite is the system
    of record; nothing is stored app-side). Bond orders are resolved
    back to their ISIN/issuer; other instruments pass through plain."""
    orders = _kite_call(kite_connect.list_orders, user_id)
    bonds = queries.symbol_bond_map()
    for o in orders:
        hit = bonds.get(((o.get("exchange") or "").upper(),
                         o.get("tradingsymbol") or ""))
        o["isin"] = hit["isin"] if hit else None
        o["issuer_name"] = hit["issuer_name"] if hit else None
    return {"orders": orders}


@router.put("/api/kite/orders/{order_id}", response_model=models.KiteOrderId)
def kite_modify_order(order_id: str, body: models.KiteOrderModify,
                      user_id: int = Depends(auth.require_user)):
    """Change an open order's quantity/limit price (LIMIT/DAY stays)."""
    new_id = _kite_call(kite_connect.modify_order, user_id, order_id,
                        body.quantity, body.price)
    return {"order_id": new_id}


@router.delete("/api/kite/orders/{order_id}", response_model=models.Ok)
def kite_cancel_order(order_id: str,
                      user_id: int = Depends(auth.require_user)):
    _kite_call(kite_connect.cancel_order, user_id, order_id)
    return {"ok": True}


@router.get("/api/kite/margins", response_model=models.KiteMargins)
def kite_margins(user_id: int = Depends(auth.require_user)):
    """Equity-segment funds snapshot from the user's Zerodha account."""
    return _kite_call(kite_connect.margins, user_id)


@app.websocket("/api/kite/depth/{isin}")
async def kite_depth_stream(ws: WebSocket, isin: str):
    """Live depth for one bond, streamed from the user's own Kite
    ticker connection. Closes 4401 without a valid session, 4404 when
    Kite can't serve this bond (frontend then uses the REST path)."""
    user_id = auth.parse_session(ws.cookies.get(auth.SESSION_COOKIE) or "")
    if user_id is None:
        await ws.close(code=4401)
        return
    isin = isin.strip().upper()
    row = await run_in_threadpool(queries.bond_row, isin)
    access_token = (await run_in_threadpool(kite_connect.token_for, user_id)
                    if kite_connect.configured() else None)
    if row is None or access_token is None:
        await ws.close(code=4404)
        return
    nse_hit = await run_in_threadpool(
        kite.resolve_nse, row.get("nse_symbol"), row.get("nse_series"))
    bse_hit = await run_in_threadpool(kite.resolve_bse, row.get("bse_scrip_id"))
    tokens = {hit[1]: name
              for name, hit in (("nse", nse_hit), ("bse", bse_hit)) if hit}
    if not tokens:
        await ws.close(code=4404)
        return

    await ws.accept()
    today = date.today()
    sched = await run_in_threadpool(
        lambda: schedule.build(isin, queries.bond_cashflows(isin),
                               queries.bond_terms(isin), today=today))
    ytm_memo: dict[float, float | None] = {}
    sides: dict[str, dict | None] = {"nse": None, "bse": None}
    conn, queue = await kite_ticker.manager.attach(user_id, access_token,
                                                   set(tokens))
    recv_task = asyncio.create_task(ws.receive())  # detect client close
    try:
        while True:
            queue_task = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                {recv_task, queue_task}, return_when=asyncio.FIRST_COMPLETED)
            if recv_task in done:  # client closed (or sent something)
                queue_task.cancel()
                break
            tick = queue_task.result()
            if tick is None:  # Kite connection died; frontend falls back
                break
            sides[tokens[tick["token"]]] = kite_ticker.tick_to_side(tick)
            ask_prices = sorted({
                lv["price"] for side in sides.values() if side
                for lv in side["asks"] if lv["price"]
            })
            if sched.usable:
                for p in ask_prices:
                    if p not in ytm_memo:
                        ytm_memo[p] = yields.exact_ytm(p, sched, today)
            await ws.send_json({
                "isin": isin,
                "fetched_at": datetime.now(UTC).isoformat(),
                "nse": sides["nse"],
                "bse": sides["bse"],
                "ytm_at_ask": [{"price": p, "ytm": ytm_memo.get(p)}
                               for p in ask_prices],
            })
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        recv_task.cancel()
        await kite_ticker.manager.detach(user_id, conn, queue)
        with contextlib.suppress(Exception):
            await ws.close()


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
