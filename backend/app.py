"""BondLens API — read-side over the scheduled data platform.

No fetch endpoints: the jobs container keeps the database current
(universe daily, NSDL daily, EOD trades nightly) and precomputes the
explorer analytics into bond_analytics (scripts/compute_analytics.py).
The API is stateless: the explorer is a filtered SELECT; only the
per-bond detail view computes yields live (it supports what-if pricing).
"""
import asyncio
import contextlib
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

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

from . import (
    ask_ytm,
    auth,
    db,
    depth,
    exchange_evidence,
    fills,
    kite,
    kite_connect,
    kite_ticker,
    models,
    ntfy,
    opportunity_rules,
    queries,
    quote_time,
    safe_errors,
)
from .analytics import portfolio as pf
from .analytics import ratings, schedule, valuation, yields
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
    schedules_by_isin: dict[str, schedule.Schedule] = {}
    for isin in isins:  # a portfolio holds few ISINs; per-ISIN reads are fine
        core_by_isin[isin] = queries.analytics_row(isin, STALE_AFTER_DAYS) or {}
        sched = schedule.build(isin, queries.bond_cashflows(isin),
                               queries.bond_terms(isin), today=today)
        flows_by_isin[isin] = sched.flows
        schedules_by_isin[isin] = sched

    positions = pf.build_positions(lots, core_by_isin)
    for p in positions:
        p["next_payment"] = pf.next_payment(p["lots"],
                                            flows_by_isin[p["isin"]], today)
        sched = schedules_by_isin[p["isin"]]
        p["yield_on_cost"] = (pf.yield_on_cost(p["lots"], sched.flows, today)
                              if sched.usable and not sched.perpetual
                              and not sched.pricing_issues(today) else None)
        p["ytm_effective"] = (yields.effective_annual(p["ytm"], sched.periods_per_year or 1)
                              if sched.usable and not sched.pricing_issues(today)
                              and core_by_isin[p["isin"]].get("frequency") == sched.frequency else None)
        p["yield_on_cost_notes"] = ["Projected XIRR uses scheduled cashflows, not verified bank receipts"]
        if any(f.optional for f in sched.flows):
            p["yield_on_cost_notes"].append("Optional full-redemption payouts are excluded from the hold-to-maturity projection")

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
def notifications(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                  user_id: int = Depends(auth.require_user)):
    """Newest-first portfolio notification feed with the unread count.
    Rows are produced by the scheduled generator, not this API."""
    return db.get_notifications(user_id, limit, offset)


@router.post("/api/notifications/read", response_model=models.Ok)
def notifications_read(body: models.NotificationsReadRequest | None = None,
                       user_id: int = Depends(auth.require_user)):
    """Mark only the notification IDs explicitly acknowledged by the user."""
    db.mark_notifications_read(user_id, body.ids if body else [])
    return {"ok": True}


# --------------------------------------------------------------------------
# Opportunity alerts (rules, exit targets, ntfy topic) — per user

def _ist_today() -> date:
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def _rules_call(fn, *args):
    try:
        return fn(*args)
    except opportunity_rules.RuleError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e


@router.get("/api/alerts", response_model=models.AlertsOverview)
def alerts_overview(user_id: int = Depends(auth.require_user)):
    """Everything the Alerts panel shows: rules, exit targets, the push
    topic, recent hits with their push latency, and the watcher's pulse."""
    return {"rules": opportunity_rules.list_rules(user_id),
            "targets": opportunity_rules.list_targets(user_id),
            "settings": {"ntfy_topic": opportunity_rules.get_topic(user_id)},
            "recent": opportunity_rules.recent_hits(user_id),
            "hits_today": opportunity_rules.hits_today(user_id, _ist_today()),
            "watcher": opportunity_rules.read_heartbeat()}


@router.post("/api/alerts/rules", response_model=models.AlertRule, status_code=201)
def alerts_create_rule(body: models.AlertRuleIn, user_id: int = Depends(auth.require_user)):
    return _rules_call(opportunity_rules.create_rule, user_id, body.model_dump())


@router.patch("/api/alerts/rules/{rule_id}", response_model=models.AlertRule)
def alerts_update_rule(rule_id: int, body: models.AlertRulePatch,
                       user_id: int = Depends(auth.require_user)):
    return _rules_call(opportunity_rules.update_rule, user_id, rule_id,
                       body.model_dump(exclude_unset=True))


@router.delete("/api/alerts/rules/{rule_id}", response_model=models.Ok)
def alerts_delete_rule(rule_id: int, user_id: int = Depends(auth.require_user)):
    _rules_call(opportunity_rules.delete_rule, user_id, rule_id)
    return {"ok": True}


@router.put("/api/alerts/targets/{isin}", response_model=models.ExitTarget)
def alerts_put_target(isin: str, body: models.ExitTargetIn,
                      user_id: int = Depends(auth.require_user)):
    isin = isin.strip().upper()
    if queries.bond_row(isin) is None:
        raise HTTPException(status_code=404, detail="Unknown ISIN")
    return _rules_call(opportunity_rules.put_target, user_id, isin, body.model_dump())


@router.delete("/api/alerts/targets/{isin}", response_model=models.Ok)
def alerts_delete_target(isin: str, user_id: int = Depends(auth.require_user)):
    _rules_call(opportunity_rules.delete_target, user_id, isin.strip().upper())
    return {"ok": True}


@router.put("/api/alerts/settings", response_model=models.AlertSettings)
def alerts_put_settings(body: models.AlertSettings, user_id: int = Depends(auth.require_user)):
    return {"ntfy_topic": _rules_call(opportunity_rules.set_topic, user_id, body.ntfy_topic)}


@router.post("/api/alerts/test", response_model=models.Ok)
def alerts_test_push(user_id: int = Depends(auth.require_user)):
    """One test push to the user's topic so they can confirm the phone side."""
    topic = opportunity_rules.get_topic(user_id)
    if not topic:
        raise HTTPException(status_code=400, detail="Set an ntfy topic first")
    try:
        ntfy.push(topic, "BondLens alerts are wired", "Opportunity pushes will arrive here. "
                  "Turn on Instant delivery for this topic in the ntfy app for the fastest path.",
                  priority=ntfy.PRIORITY_HIGH, tags="bell,bondlens")
    except Exception as exc:  # noqa: BLE001 - ntfy outage is the user's answer, not a 500
        raise HTTPException(status_code=502, detail=f"ntfy rejected the push: {safe_errors.error_summary(exc)}") from exc
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
        None, pattern="^(bullet|staggered|maturity|perpetual)$",
        description="Payout structure: bullet, staggered, "
                    "maturity (zero-coupon/cumulative) or perpetual"),
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
    # Effectively unbounded for the ~6.6k universe: the explorer fetches
    # everything and truncates AFTER ordering, client-side — a page cut
    # before ordering hides whatever the cut order pushed past the edge.
    limit: int = Query(500, le=100_000),
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
def bond_detail(isin: str, price: float | None = Query(None, gt=0, allow_inf_nan=False)):
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
    coupon = sched.coupon_rate
    face = sched.face_value

    red_date = terms.get("redemption_date") if terms else None
    perpetual = bool(terms and terms.get("perpetual")) or (
        red_date is not None and red_date.year >= 2500)
    if perpetual:
        red_date = None
    call_dates = yields.parse_call_dates(terms)
    priced = valuation.calculate(ltp, sched, terms, today, allow_approx=True,
                                 otc_yield=core.get("otc_yield"), otc_trade_date=core.get("otc_trade_date"),
                                 otc_price_pct=core.get("otc_price_pct"),
                                 price_date=None if custom_price else core.get("trade_date"))
    if perpetual:
        # A maturity-based YTM against a perpetual (9999 sentinel) is
        # spurious — yield-to-call is the meaningful figure.
        exact = approx = None
        best = priced["ytm"]
        ytw = None
    else:
        best = priced["ytm"]
        exact = best if priced["ytm_method"] in ("exact", "estimated") else None
        approx = best if priced["ytm_method"] == "approx" else None
        ytw = (yields.yield_to_worst(ltp, sched, call_dates, today)
               if ltp and call_dates else None)
    future_calls = [d for d in call_dates if d > today]
    pricing_issues = sched.pricing_issues(today, until=future_calls[0] if perpetual and future_calls else None)
    pricing_notes = list(pricing_issues)
    if yields.call_terms_unresolved(terms):
        ytw = None
        pricing_notes.append("Call terms are unresolved or recurring; only explicit dates can be priced, and full yield-to-worst is unavailable")
    if future_calls and ltp and ((perpetual and best is None) or (not perpetual and ytw is None)):
        pricing_notes.append("Call yield unavailable: verify coupon coverage, principal balance, call price/premium, and call-date payments")
    elif future_calls and ltp:
        pricing_notes.append("Call scenarios assume redemption of outstanding face value; verify the contractual call price")
    accrued = yields.accrued_interest(sched, today)

    flows_out = []
    future_total = 0.0
    for f in sched.flows:
        fut = f.receivable_by_buyer(today)
        if fut and f.amount:
            future_total += f.amount
        flows_out.append({
            "event": f.event, "due_date": f.due_date,
            "record_date": f.record_date, "payment_date": f.payment_date,
            "amount": f.amount, "estimated": f.estimated, "future": fut,
            "optional": f.optional, "adjustments": f.adjustments,
        })
    next_payment = next((f for f in flows_out if f["future"]), None)

    return {
        **row,
        "as_of": today,
        "terms": terms,
        "trustee_name": queries.bond_trustee(isin),
        # Precomputed on the analytics row; live lookup only if that is empty
        # (e.g. Kite's instrument list was unreachable at the last refresh).
        "kite_url": core.get("kite_url") or kite.kite_url(
            row.get("nse_symbol"), row.get("nse_series"), row.get("bse_scrip_id"),
            prefer=core.get("exchange")),
        "ltp": ltp,
        "custom_price": custom_price,
        "trade_date": core.get("trade_date"),
        "days_since_traded": core.get("days_since_traded"),
        "volume": core.get("volume"),
        "turnover": core.get("turnover"),
        "num_trades": core.get("num_trades"),
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
        "schedule_usable": sched.usable and not pricing_issues,
        "schedule_warnings": list(dict.fromkeys(sched.warnings + pricing_notes)),
        "amounts_estimated": sched.amounts_estimated,
        "yield_amounts_estimated": sched.yield_amounts_estimated(
            today, until=future_calls[0] if perpetual and future_calls else None),
        "issue_date": terms.get("allotment_date") if terms else None,
        "maturity_date": red_date,
        "exchange": core.get("exchange"),
        "years_to_maturity": _years(red_date, today),
        "ytm_exact": exact,
        "ytm_approx": approx,
        "ytm": best,
        "ytm_reliable": priced["ytm_reliable"],
        "valuation": priced,
        "yield_to_worst": ytw,
        "call_dates": call_dates,
        "current_yield": yields.current_yield(ltp, coupon, face),
        "accrued": accrued,
        "clean_price": yields.clean_price(ltp, sched, today) if ltp else None,
        "duration": yields.modified_duration(ltp, sched, best, today) if priced["ytm_method"] in ("exact", "estimated") else None,
        "credit_rating": ratings.composite(cur),
        "rating_rank": ratings.best_rank(cur),
        "in_default": ratings.in_default(cur) or bool(extras["defaults"]),
        "tech_delay": bool(extras["technical_defaults"]),
        "rating_timeline": ratings.timeline(hist),
        "cashflows": flows_out,
        "pricing_cashflows": [valuation.flow_data(f, today) for f in valuation.scenario_flows(sched, terms, today) or []],
        "input_cashflows": raw_flows,
        "future_total_per_unit": (round(future_total, 2) if sched.usable and not perpetual
                                  and not sched.pricing_issues(today, check_convention=False) else None),
        "next_payment": next_payment,
        **extras,
        "issuer_bonds": queries.issuer_siblings(isin, row["issuer_name"])
                        if row.get("issuer_name") else [],
    }


# --------------------------------------------------------------------------
# Trade history
# --------------------------------------------------------------------------

@router.get("/api/bonds/{isin}/cashflow-evidence", response_model=models.CashflowEvidenceResponse)
def bond_cashflow_evidence(
    isin: str,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
):
    today = date.today()
    start = date_from or today
    if start.year >= 2500:
        raise HTTPException(status_code=422, detail="Evidence window requires a valid calendar date")
    end = date_to or start + timedelta(days=90)
    if end < start or (end - start).days > 366 or end.year >= 2500:
        raise HTTPException(status_code=422, detail="Evidence window must be valid and at most 366 days")
    isin = isin.strip().upper()
    inputs = queries.cashflow_evidence_inputs(isin)
    if inputs is None:
        raise HTTPException(status_code=404, detail="Unknown ISIN")
    sched = schedule.build(isin, inputs["cashflows"], inputs["terms"], today=today)
    availability = exchange_evidence.source_availability(inputs["availability"], datetime.now(UTC))
    return exchange_evidence.build_evidence(
        isin, sched.flows, inputs["records"], as_of=today, date_from=start, date_to=end,
        availability=availability, partial=inputs["partial"],
    )


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

@router.get("/api/bonds/{isin}/valuation", response_model=models.YtmAtPrice)
def bond_valuation(isin: str, price: float = Query(gt=0, allow_inf_nan=False)):
    isin = isin.strip().upper()
    if queries.bond_row(isin) is None:
        raise HTTPException(status_code=404, detail="Unknown ISIN")
    today = date.today()
    terms = queries.bond_terms(isin)
    sched = schedule.build(isin, queries.bond_cashflows(isin), terms, today=today)
    core = queries.analytics_row(isin, STALE_AFTER_DAYS) or {}
    return valuation.calculate(price, sched, terms, today, otc_yield=core.get("otc_yield"),
                               otc_trade_date=core.get("otc_trade_date"),
                               otc_price_pct=core.get("otc_price_pct"))


def _annotate_ytm(book: dict, isin: str) -> None:
    """Attach indicative gross valuations, including their assumptions,
    to positive-size resting ask prices (dirty)."""
    sides = [book.get("nse"), book.get("bse")]
    for side in sides:
        if side is not None:
            side.update(quote_time.metadata(side.get("fetched_at") or book.get("fetched_at"),
                                            side.get("quote_at") or side.get("as_of")))
    prices = ask_ytm.ask_prices(sides)
    book["ytm_at_ask"] = ask_ytm.PriceCalculator(isin).values(prices, date.today()) if prices else []


@router.get("/api/bonds/{isin}/depth", response_model=models.OrderBook)
def bond_depth(isin: str, user_id: int = Depends(auth.require_user)):
    """Live top-5 bid/ask depth from both exchanges — via the user's
    own Kite session when connected, exchange scrapers otherwise —
    fetched on demand (30s cache). Ask levels are annotated with the
    indicative gross YTM or conditional YTC at that modeled dirty price."""
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
    of record for orders). Bond orders are resolved back to their
    ISIN/issuer; filled bond BUYs are reconciled into portfolio lots
    (new_lots tells the UI when to refresh the portfolio)."""
    orders = _kite_call(kite_connect.list_orders, user_id)
    bonds = queries.symbol_bond_map()
    for o in orders:
        hit = bonds.get(((o.get("exchange") or "").upper(),
                         o.get("tradingsymbol") or ""))
        o["isin"] = hit["isin"] if hit else None
        o["issuer_name"] = hit["issuer_name"] if hit else None
    pending = False
    try:
        new_lots = fills.process_orders(user_id, orders)
    except Exception:  # noqa: BLE001 - reconciliation must never break the list
        new_lots, pending = 0, True
    return {"orders": orders, "new_lots": new_lots, "reconciliation_pending": pending}


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


@router.post("/api/kite/ask-ytm", response_model=models.AskYtmResponse)
def kite_ask_ytm(body: models.AskYtmRequest,
                 user_id: int = Depends(auth.require_user)):
    """Positive-size best-ask snapshots with indicative gross valuations,
    their assumptions and capture times, under the user's Kite session.
    `page` walks the whole listed universe (the explorer captures every
    bond once per load); `isins` asks for specific bonds. Empty when Kite
    isn't connected or no usable ask is available."""
    if not kite_connect.configured() or kite_connect.token_for(user_id) is None:
        return {"items": []}
    if body.page is not None:
        return ask_ytm.compute_page(user_id, body.page)
    return {"items": ask_ytm.compute(user_id, body.isins)}


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
    calculator = ask_ytm.PriceCalculator(isin)
    sides: dict[str, dict | None] = {"nse": None, "bse": None}
    fetched_at = datetime.now(UTC).isoformat()
    conn, queue = await kite_ticker.manager.attach(user_id, access_token,
                                                   set(tokens))
    recv_task = asyncio.create_task(ws.receive())  # detect client close
    try:
        while True:
            queue_task = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                {recv_task, queue_task}, timeout=30, return_when=asyncio.FIRST_COMPLETED)
            if recv_task in done:  # client closed (or sent something)
                queue_task.cancel()
                break
            if queue_task in done:
                tick = queue_task.result()
                if tick is None:  # Kite connection died; frontend falls back
                    break
                fetched_at = datetime.now(UTC).isoformat()
                side = kite_ticker.tick_to_side(tick)
                if side is not None:
                    side.update(quote_time.metadata(fetched_at, side.get("as_of")))
                sides[tokens[tick["token"]]] = side
            else:
                queue_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await queue_task
            for side in sides.values():
                if side is not None:
                    side.update(quote_time.metadata(side.get("fetched_at"), side.get("quote_at")))
            priced = await run_in_threadpool(calculator.values, ask_ytm.ask_prices(sides.values()), date.today())
            await ws.send_json({"isin": isin, "fetched_at": fetched_at,
                                "nse": sides["nse"], "bse": sides["bse"], "ytm_at_ask": priced})
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
