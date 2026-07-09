"""FastAPI app: refresh NSE data, query/screen bonds, serve the dashboard."""
import re
import threading
import time
from datetime import date
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, gsec, kite, nsdl, nse
from .ytm import (accrued_interest, exact_ytm, modified_duration, parse_date,
                  years_to_maturity, ytm)

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


RATING_FLAG_DAYS = 30  # how long an up/downgrade marker stays visible
STALE_AFTER_DAYS = 3   # LTP considered stale after this many days without trades


def annotate_signals(r: Dict, changes: Dict, last_traded: Dict):
    """Attach rating-change and stale-price signals to a bond row in place."""
    today = date.today()
    r["rating_change"] = None
    r["rating_prev"] = None
    r["rating_changed_at"] = None
    ch = changes.get(r["isin"])
    if ch:
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

_nsdl_state = {"running": False, "done": 0, "total": 0, "errors": 0, "current": None}
_nsdl_lock = threading.Lock()


def _nsdl_worker(isins):
    for isin in isins:
        _nsdl_state["current"] = isin
        try:
            meta, flows = nsdl.fetch_schedule(isin)
            db.save_nsdl(isin, "ok" if flows else "empty", meta, flows)
        except Exception:  # noqa: BLE001 - record and continue
            db.save_nsdl(isin, "error", {}, [])
            _nsdl_state["errors"] += 1
        _nsdl_state["done"] += 1
        time.sleep(NSDL_DELAY_SECONDS)
    _nsdl_state["running"] = False
    _nsdl_state["current"] = None


@app.post("/api/nsdl/fetch")
def nsdl_fetch():
    """Start background fetch of NSDL schedules for ISINs not yet cached."""
    with _nsdl_lock:
        if _nsdl_state["running"]:
            return {"started": False, "reason": "already running", **_nsdl_state}
        fetched = db.nsdl_fetched_isins()
        have_flows = db.isins_with_flows()
        # Complete = fetched OK with an actual schedule. Everything else
        # (never tried, errored, or schedule came back empty) is retried.
        complete = {i for i, s in fetched.items() if s == "ok" and i in have_flows}
        pending = sorted(
            {r["isin"] for r in db.latest_snapshot() if r["isin"]} - complete
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
    out = []
    for r in rows:
        r["company_name"] = names.get(r["symbol"])
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
        annotate_signals(r, changes, last_traded)
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
    row["rating_rank"] = rating_rank(row["credit_rating"])
    annotate_signals(row, db.rating_changes(), db.last_traded_map())

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
    }


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
