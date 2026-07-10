"""FastAPI app: refresh NSE data, query/screen bonds, serve the dashboard."""
import re
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, gsec, kite, nsdl, nse, pnl
from .ytm import (accrued_interest, exact_ytm, modified_duration, parse_date,
                  xirr, years_to_maturity, ytm)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="BondLens")

# Best (lowest index) to worst. Outlooks like "/Stable" are stripped before lookup.
RATING_ORDER = [
    "AAA", "AA+", "AA", "AA-", "A+", "A", "A-",
    "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-", "B+", "B", "B-", "C", "D",
]
RATING_RANK = {r: i for i, r in enumerate(RATING_ORDER)}


def rating_rank(credit_rating: Optional[str]) -> Optional[int]:
    """Best rank among possibly multiple agency ratings, e.g. 'AA/Stable, AA+/Negative'."""
    if not credit_rating:
        return None
    ranks = []
    for part in credit_rating.split(","):
        grade = part.strip().split("/")[0].strip().upper()
        if grade in RATING_RANK:
            ranks.append(RATING_RANK[grade])
    return min(ranks) if ranks else None


def coupon_from_symbol(symbol: Optional[str]) -> Optional[float]:
    """NSE bond symbols encode the coupon: 94SFL28=9.4%, 1015SCL29B=10.15%.
    Used only when NSE's coupon field is blank."""
    m = re.match(r"^(\d{2,4})[A-Z]", symbol or "")
    if not m:
        return None
    digits = m.group(1)
    value = int(digits) / (10 ** (len(digits) - 1))
    return value if 0 < value < 20 else None


def apply_nsdl_rating(r: Dict, nr: Optional[List[Dict]]):
    """Override NSE's rating fields with NSDL's agency-verified data in place.

    NSDL's ratings are current; NSE's rating field is often stale at-issuance
    data (e.g. AAA shown for years after a downgrade). Also exposes `ratings`,
    one entry per agency, so the UI can link each agency's own press release
    instead of collapsing multiple ratings into a single link.
    """
    if nr:
        r["credit_rating"] = ", ".join(
            x["rating"] + (f"/{x['outlook']}" if x["outlook"] else "")
            for x in nr)
        r["rating_agency"] = ", ".join(x["agency"] or "" for x in nr)
        r["rating_source"] = "NSDL"
        r["rating_url"] = next(
            (x["press_release"] for x in nr if x["press_release"]), None)
        r["ratings"] = [
            {"agency": x["agency"],
             "rating": x["rating"] + (f"/{x['outlook']}" if x["outlook"] else ""),
             "press_release": x["press_release"]}
            for x in nr]
    else:
        r["rating_source"] = "NSE"
        r["rating_url"] = None
        r["ratings"] = None


RATING_FLAG_DAYS = 30  # how long an up/downgrade marker stays visible
STALE_AFTER_DAYS = 3   # LTP considered stale after this many days without trades


def annotate_signals(r: Dict, changes: Dict, last_traded: Dict,
                     nsdl_ratings: Optional[Dict] = None):
    """Attach rating-change and stale-price signals to a bond row in place.

    NSDL's agency-verified rating actions are preferred; snapshot diffing of
    NSE's rating string is the fallback (NSE often never updates it).
    """
    today = date.today()
    r["rating_change"] = None
    r["rating_prev"] = None
    r["rating_changed_at"] = None
    nr = (nsdl_ratings or {}).get(r["isin"]) or []
    recent = [
        x for x in nr
        if x["changed_at"] and x["rating_action"]
        and (today - date.fromisoformat(x["changed_at"])).days <= RATING_FLAG_DAYS
        and ("UPGRADE" in x["rating_action"].upper()
             or "DOWNGRADE" in x["rating_action"].upper())
    ]
    ch = changes.get(r["isin"])
    if recent:
        downs = [x for x in recent if "DOWN" in x["rating_action"].upper()]
        pick = downs[0] if downs else recent[0]
        r["rating_change"] = "down" if downs else "up"
        r["rating_prev"] = pick["prev_rating"]
        r["rating_changed_at"] = pick["changed_at"]
    elif not nr and ch:
        prev_rank = rating_rank(ch["previous"])
        cur_rank = rating_rank(r["credit_rating"])
        age = (today - date.fromisoformat(ch["changed_at"][:10])).days
        if (prev_rank is not None and cur_rank is not None
                and prev_rank != cur_rank and age <= RATING_FLAG_DAYS):
            r["rating_change"] = "up" if cur_rank < prev_rank else "down"
            r["rating_prev"] = ch["previous"]
            r["rating_changed_at"] = ch["changed_at"][:10]
    lt = last_traded.get(r["isin"])
    r["days_since_traded"] = (
        (today - date.fromisoformat(lt[:10])).days if lt else None
    )


NSDL_DELAY_SECONDS = 10

# Ratings change over time (unlike payment schedules), so re-fetch them
# once they're older than this.
RATINGS_MAX_AGE_DAYS = 30

_nsdl_state = {"running": False, "done": 0, "total": 0, "errors": 0, "current": None}
_nsdl_lock = threading.Lock()


def _nsdl_worker(isins):
    complete = _nsdl_complete_isins()
    for isin in isins:
        _nsdl_state["current"] = isin
        if isin not in complete:  # schedules are static; don't refetch good ones
            try:
                meta, flows = nsdl.fetch_schedule(isin)
                db.save_nsdl(isin, "ok" if flows else "empty", meta, flows)
            except Exception:  # noqa: BLE001 - record and continue
                db.save_nsdl(isin, "error", {}, [])
                _nsdl_state["errors"] += 1
        try:
            current, history = nsdl.fetch_rating_data(isin)
            db.save_nsdl_ratings(isin, current)
            db.save_nsdl_rating_history(isin, history)
        except Exception:  # noqa: BLE001 - keep whatever we had
            pass
        _nsdl_state["done"] += 1
        time.sleep(NSDL_DELAY_SECONDS)
    _nsdl_state["running"] = False
    _nsdl_state["current"] = None


def _nsdl_complete_isins() -> set:
    """ISINs fetched OK with an actual schedule. Everything else (never
    tried, errored, or schedule came back empty) is worth (re)trying."""
    fetched = db.nsdl_fetched_isins()
    have_flows = db.isins_with_flows()
    return {i for i, s in fetched.items() if s == "ok" and i in have_flows}


def _fetch_nsdl_async(isin: str):
    """One-off background fetch for a single ISIN. Holdings outside the NSE
    snapshot would otherwise stay uncached forever. A transient failure must
    not clobber a previously good cache entry."""
    def run():
        try:
            meta, flows = nsdl.fetch_schedule(isin)
            db.save_nsdl(isin, "ok" if flows else "empty", meta, flows)
        except Exception:  # noqa: BLE001 - record and move on
            if db.nsdl_fetched_isins().get(isin) != "ok":
                db.save_nsdl(isin, "error", {}, [])
        try:
            current, history = nsdl.fetch_rating_data(isin)
            db.save_nsdl_ratings(isin, current)
            db.save_nsdl_rating_history(isin, history)
        except Exception:  # noqa: BLE001 - keep whatever we had
            pass
    threading.Thread(target=run, daemon=True).start()


def _ratings_stale_isins(universe: set) -> set:
    fetched = db.nsdl_ratings_fetched()
    cutoff = time.time() - RATINGS_MAX_AGE_DAYS * 86400
    stale = set()
    for isin in universe:
        ts = fetched.get(isin)
        if ts is None:
            stale.add(isin)
            continue
        if datetime.fromisoformat(ts).timestamp() < cutoff:
            stale.add(isin)
    return stale


@app.post("/api/nsdl/fetch")
def nsdl_fetch():
    """Start background fetch of NSDL schedules for ISINs not yet cached.
    Covers the NSE snapshot plus any portfolio holdings outside it."""
    with _nsdl_lock:
        if _nsdl_state["running"]:
            return {"started": False, "reason": "already running", **_nsdl_state}
        universe = {r["isin"] for r in db.latest_snapshot() if r["isin"]}
        universe |= {h["isin"] for h in db.holdings()}
        pending = sorted(
            (universe - _nsdl_complete_isins()) | _ratings_stale_isins(universe)
        )
        if not pending:
            return {"started": False, "reason": "nothing to fetch", **_nsdl_state}
        _nsdl_state.update(
            {"running": True, "done": 0, "total": len(pending), "errors": 0}
        )
        threading.Thread(target=_nsdl_worker, args=(pending,), daemon=True).start()
    return {"started": True, **_nsdl_state}


@app.get("/api/nsdl/status")
def nsdl_status():
    fetched = db.nsdl_fetched_isins()
    have_flows = db.isins_with_flows()
    complete = {i for i, s in fetched.items() if s == "ok" and i in have_flows}
    snapshot_isins = {r["isin"] for r in db.latest_snapshot() if r["isin"]}
    return {
        **_nsdl_state,
        "cached_ok": len(complete),
        "cached_error": len(fetched) - len(complete),
        "snapshot_isins": len(snapshot_isins),
        "missing": len(snapshot_isins - complete),
    }


@app.post("/api/refresh")
def refresh():
    """Fetch live data from NSE, compute YTM, store a snapshot."""
    try:
        payload = nse.fetch_bonds()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"NSE fetch failed: {exc}")
    records = nse.normalize(payload)
    for r in records:
        r["ytm"] = ytm(r["ltp"], r["coupon"], r["face_value"], r["maturity_date"])
    ts = db.save_snapshot(records)
    # Company-name master and G-Sec curve alongside; both best-effort.
    try:
        master = nse.fetch_debt_master()
        if master:
            db.save_debt_master(master)
    except Exception:  # noqa: BLE001
        pass
    gsec_points = 0
    try:
        curve = gsec.fetch_curve()
        if curve:
            db.save_gsec_curve(curve)
            gsec_points = len(curve)
    except Exception:  # noqa: BLE001
        pass
    return {"fetched_at": ts, "count": len(records), "gsec_points": gsec_points}


@app.get("/api/bonds")
def bonds(
    min_ytm: Optional[float] = Query(None),
    max_ytm: Optional[float] = Query(None),
    rating: Optional[str] = Query(None, description="Substring match, e.g. AA"),
    min_rating: Optional[str] = Query(None, description="Quality floor, e.g. AA- includes AAA..AA-"),
    max_years: Optional[float] = Query(None),
    min_years: Optional[float] = Query(None),
    min_spread: Optional[float] = Query(None, description="Min spread over G-Sec, pp"),
    max_ltp: Optional[float] = Query(None, description="Max price per unit (ticket size)"),
    min_traded_value: Optional[float] = Query(None),
    watchlist_only: bool = Query(False),
    below_par: bool = Query(False, description="Only bonds trading below face value"),
    fresh_only: bool = Query(False, description="Exclude stale prices"),
    exact_only: bool = Query(False, description="Only bonds with exact (NSDL) YTM"),
    downgraded_only: bool = Query(False, description="Only recently downgraded bonds"),
    frequency: Optional[str] = Query(None, description="Coupon frequency, e.g. monthly"),
):
    rows = db.latest_snapshot()
    watch = set(db.get_watchlist())
    meta_map = db.nsdl_meta_map()
    flows_map = db.nsdl_all_cashflows()
    curve = db.latest_gsec_curve()
    changes = db.rating_changes()
    last_traded = db.last_traded_map()
    names = db.company_names()
    nsdl_ratings = db.nsdl_ratings_map()
    out = []
    for r in rows:
        r["company_name"] = names.get(r["symbol"])
        apply_nsdl_rating(r, nsdl_ratings.get(r["isin"]))
        maturity = parse_date(r["maturity_date"])
        r["years_to_maturity"] = (
            round(years_to_maturity(maturity), 2) if maturity else None
        )
        r["in_watchlist"] = r["isin"] in watch
        r["kite_url"] = kite.kite_url(r["symbol"], r["series"])
        r["rating_rank"] = rating_rank(r["credit_rating"])
        r["current_yield"] = (
            round(r["coupon"] * r["face_value"] / r["ltp"], 2)
            if r["coupon"] and r["face_value"] and r["ltp"] else None
        )
        meta = meta_map.get(r["isin"])
        r["frequency"] = (
            nsdl.classify_frequency(meta["frequency_text"]) if meta else None
        )
        flows, _ = nsdl.fill_amounts(
            flows_map.get(r["isin"], []),
            r["coupon"] or coupon_from_symbol(r["symbol"]),
            r["face_value"],
            r["frequency"],
        )
        flows = nsdl.normalize_flows(flows, r["face_value"])
        flows_ok = nsdl.redemption_matches_face(flows, r["face_value"])
        exact = None
        if meta and meta["status"] == "ok" and flows_ok:
            exact = exact_ytm(r["ltp"], flows)
        if exact is not None:
            r["ytm"] = exact
            r["ytm_method"] = "exact"
        else:
            r["ytm_method"] = "approx" if r["ytm"] is not None else None
        r["duration"] = (
            modified_duration(r["ltp"], flows, r["ytm"])
            if meta and meta["status"] == "ok" and flows_ok else None
        )
        gy = gsec.interpolate(curve, r["years_to_maturity"])
        r["spread"] = (
            round(r["ytm"] - gy, 2) if r["ytm"] is not None and gy is not None
            else None
        )
        annotate_signals(r, changes, last_traded, nsdl_ratings)
        if watchlist_only and not r["in_watchlist"]:
            continue
        if min_ytm is not None and (r["ytm"] is None or r["ytm"] < min_ytm):
            continue
        if max_ytm is not None and (r["ytm"] is None or r["ytm"] > max_ytm):
            continue
        if rating and rating.upper() not in (r["credit_rating"] or "").upper():
            continue
        if min_rating is not None:
            floor = RATING_RANK.get(min_rating.strip().upper())
            rank = rating_rank(r["credit_rating"])
            if floor is None or rank is None or rank > floor:
                continue
        if max_years is not None and (
            r["years_to_maturity"] is None or r["years_to_maturity"] > max_years
        ):
            continue
        if min_years is not None and (
            r["years_to_maturity"] is None or r["years_to_maturity"] < min_years
        ):
            continue
        if min_spread is not None and (
            r["spread"] is None or r["spread"] < min_spread
        ):
            continue
        if max_ltp is not None and (r["ltp"] is None or r["ltp"] > max_ltp):
            continue
        if fresh_only and not (
            (r["traded_value"] or 0) > 0
            or (r["days_since_traded"] is not None
                and r["days_since_traded"] <= STALE_AFTER_DAYS)
        ):
            continue
        if exact_only and r["ytm_method"] != "exact":
            continue
        if downgraded_only and r["rating_change"] != "down":
            continue
        if min_traded_value is not None and (
            r["traded_value"] is None or r["traded_value"] < min_traded_value
        ):
            continue
        if below_par and not (
            r["ltp"] is not None and r["face_value"] and r["ltp"] < r["face_value"]
        ):
            continue
        if frequency and r["frequency"] != frequency.lower():
            continue
        out.append(r)
    out.sort(key=lambda r: r["traded_value"] or 0, reverse=True)
    return {"count": len(out), "bonds": out}


@app.get("/api/bonds/{isin}/detail")
def bond_detail(isin: str, price: Optional[float] = Query(None, gt=0)):
    """Everything known about one bond: snapshot, NSDL meta, dated cash flows.

    Flows are flagged `future` when a buyer today would still receive them
    (record date, falling back to due date, is after today).

    ``price`` overrides the last traded price for what-if analysis: every
    price-derived figure (exact/approx YTM, clean price, duration, spread,
    and the invest projection inputs) is recomputed at that price. Market
    facts (pct_change, traded value, history) are left untouched.
    """
    snapshot = db.latest_snapshot()
    row = next((r for r in snapshot if r["isin"] == isin), None)
    if row is None:
        raise HTTPException(status_code=404, detail="ISIN not in latest snapshot")
    custom_price = price is not None
    if custom_price:
        row["ltp"] = price
        row["ytm"] = ytm(
            price,
            row["coupon"] or coupon_from_symbol(row["symbol"]),
            row["face_value"],
            row["maturity_date"],
        ) if row["face_value"] and row["maturity_date"] else None
    maturity = parse_date(row["maturity_date"])
    row["years_to_maturity"] = (
        round(years_to_maturity(maturity), 2) if maturity else None
    )
    row["kite_url"] = kite.kite_url(row["symbol"], row["series"])
    nsdl_ratings = db.nsdl_ratings_map()
    apply_nsdl_rating(row, nsdl_ratings.get(isin))
    row["rating_rank"] = rating_rank(row["credit_rating"])
    annotate_signals(row, db.rating_changes(), db.last_traded_map(), nsdl_ratings)

    meta = db.nsdl_meta_map().get(isin)
    flows, amounts_estimated = nsdl.fill_amounts(
        db.nsdl_all_cashflows().get(isin, []),
        row["coupon"] or coupon_from_symbol(row["symbol"]),
        row["face_value"],
        nsdl.classify_frequency(meta["frequency_text"]) if meta else None,
    )
    flows = nsdl.normalize_flows(flows, row["face_value"])
    flows_ok = nsdl.redemption_matches_face(flows, row["face_value"])
    today = date.today()
    out_flows = []
    future_total = 0.0
    for f in sorted(flows, key=lambda f: parse_date(f["due_date"]) or today):
        due = parse_date(f["due_date"])
        record = parse_date(f.get("record_date"))
        future = bool(due and due > today) and not (record and record <= today)
        if future:
            future_total += f["amount"] or 0.0
        out_flows.append({**f, "future": future})

    exact = (
        exact_ytm(row["ltp"], flows)
        if meta and meta["status"] == "ok" and flows_ok else None
    )
    accrued = (
        accrued_interest(flows) if meta and meta["status"] == "ok" else None
    )
    best_ytm = exact if exact is not None else row["ytm"]
    duration = (
        modified_duration(row["ltp"], flows, best_ytm)
        if meta and meta["status"] == "ok" and flows_ok else None
    )
    gy = gsec.interpolate(db.latest_gsec_curve(), row["years_to_maturity"])
    next_payment = next((f for f in out_flows if f["future"]), None)

    # Sibling series from the same issuer: traded ones from the snapshot,
    # plus listed-but-untraded ones from NSE's debt master.
    master = db.debt_master()
    issuer = (master.get(row["symbol"]) or {}).get("name")
    issuer_bonds = []
    if issuer:
        snap_by_symbol = {r["symbol"]: r for r in snapshot if r["symbol"]}
        for sym, m in master.items():
            if m["name"] != issuer or sym == row["symbol"]:
                continue
            s = snap_by_symbol.get(sym)
            mat = (s or m).get("maturity_date")
            mat_d = parse_date(mat) if mat else None
            issuer_bonds.append({
                "symbol": sym,
                "isin": s["isin"] if s else None,
                "coupon": (s or m).get("coupon"),
                "maturity_date": mat,
                "years_to_maturity": (
                    round(years_to_maturity(mat_d), 2) if mat_d else None
                ),
                "ltp": s["ltp"] if s else None,
                "ytm": s["ytm"] if s else None,
                "traded_value": s["traded_value"] if s else None,
                "in_feed": s is not None,
            })
        issuer_bonds.sort(
            key=lambda b: (b["years_to_maturity"] is None,
                           b["years_to_maturity"] or 0)
        )

    return {
        **row,
        "frequency": nsdl.classify_frequency(meta["frequency_text"]) if meta else None,
        "frequency_text": meta["frequency_text"] if meta else None,
        "coupon_basis": meta["coupon_basis"] if meta else None,
        "nsdl_status": meta["status"] if meta else None,
        "ytm_exact": exact,
        "ytm_approx": row["ytm"],
        "accrued": accrued,
        "clean_price": (
            round(row["ltp"] - accrued, 2)
            if accrued is not None and row["ltp"] else None
        ),
        "duration": duration,
        "gsec_yield": gy,
        "spread": (
            round(best_ytm - gy, 2)
            if best_ytm is not None and gy is not None else None
        ),
        "cashflows": out_flows,
        "amounts_estimated": amounts_estimated,
        "schedule_mismatch": not flows_ok,
        "custom_price": custom_price,
        "future_total_per_unit": round(future_total, 2),
        "next_payment": next_payment,
        "issuer_name": issuer,
        "issuer_bonds": issuer_bonds,
        "rating_history": db.nsdl_rating_history(isin),
    }


# ---- Portfolio P&L ---------------------------------------------------------

class HoldingIn(BaseModel):
    isin: str
    qty: float
    buy_price: Optional[float] = None
    buy_date: Optional[str] = None  # YYYY-MM-DD or DD-Mon-YYYY


class HoldingPatch(BaseModel):
    qty: Optional[float] = None
    buy_price: Optional[float] = None
    buy_date: Optional[str] = None


def _validated_buy_date(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return None
    d = parse_date(value)
    if d is None:
        raise HTTPException(status_code=422, detail=f"Unparseable date: {value}")
    if d > date.today():
        raise HTTPException(status_code=422, detail="Buy date is in the future")
    return d.isoformat()


@app.get("/api/portfolio")
def portfolio():
    """All holdings with per-holding and aggregate P&L.

    Accuracy caveats ride along as `warnings` per holding: schedule quality,
    stale prices, estimated amounts, missing buy dates. TDS (~10% on
    interest) is NOT modelled — realized income is gross.
    """
    snap = {r["isin"]: r for r in db.latest_snapshot() if r["isin"]}
    meta_map = db.nsdl_meta_map()
    flows_map = db.nsdl_all_cashflows()
    last_traded = db.last_traded_map()
    names = db.company_names()
    today = date.today()
    out = []
    all_flows = []          # combined dated flows for portfolio XIRR
    xirr_complete = True    # only meaningful if every holding contributed
    for h in db.holdings():
        r = snap.get(h["isin"])
        meta = meta_map.get(h["isin"])
        coupon = face = None
        if r:
            coupon = r["coupon"] or coupon_from_symbol(r["symbol"])
            face = r["face_value"]
        flows, amounts_estimated = nsdl.fill_amounts(
            flows_map.get(h["isin"], []), coupon, face,
            nsdl.classify_frequency(meta["frequency_text"]) if meta else None,
        )
        flows = nsdl.normalize_flows(flows, face)
        nsdl_ok = bool(meta and meta["status"] == "ok" and flows)
        schedule_ok = nsdl_ok and nsdl.redemption_matches_face(flows, face)
        figs = pnl.holding_pnl(h, r, flows if nsdl_ok else [], schedule_ok)
        if amounts_estimated and nsdl_ok:
            figs["warnings"].append(
                "payment amounts estimated from the coupon rate")
        lt = last_traded.get(h["isin"])
        days_since = (today - date.fromisoformat(lt[:10])).days if lt else None
        if (days_since is not None and days_since >= STALE_AFTER_DAYS
                and not figs["closed"]):
            figs["warnings"].append(
                f"price is {days_since} days old — market value and XIRR may be off")
        buy_d = parse_date(h["buy_date"] or "")
        if buy_d and figs["invested"] and figs["market_value"] is not None:
            all_flows.append((buy_d, -figs["invested"]))
            all_flows += [
                (parse_date(f["due_date"]), h["qty"] * f["amount"])
                for f in pnl.received_flows(flows if nsdl_ok else [], buy_d)
            ]
            if not figs["closed"]:
                all_flows.append((today, figs["market_value"]))
        else:
            xirr_complete = False
        out.append({
            **h,
            **figs,
            "symbol": r["symbol"] if r else None,
            "company_name": names.get(r["symbol"]) if r else None,
            "ltp": r["ltp"] if r else None,
            "face_value": face,
            "maturity_date": r["maturity_date"] if r else None,
            "days_since_traded": days_since,
            "in_feed": r is not None,
        })

    def total(key):
        vals = [x[key] for x in out if x[key] is not None]
        return round(sum(vals), 2) if vals else None

    return {
        "holdings": out,
        "totals": {
            "invested": total("invested"),
            "market_value": total("market_value"),
            "realized": total("realized"),
            "unrealized": total("unrealized"),
            "total_pnl": total("total_pnl"),
            "xirr": xirr(all_flows) if all_flows and xirr_complete else None,
            "xirr_complete": xirr_complete,
        },
    }


@app.post("/api/portfolio/holdings")
def add_holding(body: HoldingIn):
    if body.qty <= 0:
        raise HTTPException(status_code=422, detail="Quantity must be positive")
    isin = body.isin.strip().upper()
    rowid = db.add_holding(
        isin, body.qty, body.buy_price, _validated_buy_date(body.buy_date),
    )
    # Holdings need their payment schedule for realized income; fetch it now
    # if it isn't cached (the bond may not be in the NSE snapshot at all).
    if isin not in _nsdl_complete_isins():
        _fetch_nsdl_async(isin)
    return {"id": rowid}


@app.patch("/api/portfolio/holdings/{holding_id}")
def edit_holding(holding_id: int, body: HoldingPatch):
    fields = {}
    if body.qty is not None:
        if body.qty <= 0:
            raise HTTPException(status_code=422, detail="Quantity must be positive")
        fields["qty"] = body.qty
    if body.buy_price is not None:
        fields["buy_price"] = body.buy_price
    if body.buy_date is not None:
        fields["buy_date"] = _validated_buy_date(body.buy_date)
    if not fields:
        raise HTTPException(status_code=422, detail="Nothing to update")
    if not db.update_holding(holding_id, fields):
        raise HTTPException(status_code=404, detail="Holding not found")
    return {"id": holding_id, "updated": sorted(fields)}


@app.delete("/api/portfolio/holdings/{holding_id}")
def remove_holding(holding_id: int):
    if not db.delete_holding(holding_id):
        raise HTTPException(status_code=404, detail="Holding not found")
    return {"id": holding_id, "deleted": True}


@app.get("/api/gsec")
def gsec_curve():
    """Latest stored G-Sec yield curve (for the scatter overlay)."""
    return {"curve": db.latest_gsec_curve()}


@app.get("/api/bonds/{isin}/history")
def history(isin: str):
    return {"isin": isin, "history": db.price_history(isin)}


@app.post("/api/watchlist/{isin}")
def watch(isin: str):
    db.add_to_watchlist(isin)
    return {"isin": isin, "watched": True}


@app.delete("/api/watchlist/{isin}")
def unwatch(isin: str):
    db.remove_from_watchlist(isin)
    return {"isin": isin, "watched": False}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
