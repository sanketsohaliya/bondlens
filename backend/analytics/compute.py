"""Precompute per-bond screener analytics into the bond_analytics table.

Runs inside the jobs container after the NSDL and EOD parsers (the only
events that change the inputs). The API then serves the screener as a
plain indexed SELECT — no in-process cache, no cold start.

Date-relative fields (staleness, years to maturity) are intentionally NOT
stored: they drift daily and are derived in SQL at read time. YTM is
price-dependent and refreshed here whenever new trades land, which is
exactly as often as prices can change.
"""
from datetime import UTC, date, datetime
from typing import LiteralString

from .. import db, queries
from . import ratings, schedule, yields

STALE_AFTER_DAYS = 7  # retail price older than this is flagged stale

# Table DDL lives in migrations/ (0001 baseline).
_COLS: list[LiteralString] = [
    "isin", "issuer_name", "sector", "industry", "on_nse", "on_bse",
    "nse_symbol", "nse_series", "bse_scrip_id", "coupon", "face_value",
    "coupon_basis", "frequency", "secured", "tax_free", "perpetual",
    "maturity_date", "schedule_shape", "ltp", "trade_date", "exchange",
    "volume", "turnover", "num_trades", "otc_price_pct", "otc_yield",
    "otc_trade_date", "ytm", "ytm_method", "current_yield", "duration",
    "credit_rating", "rating_rank", "rating_change", "rating_prev",
    "rating_changed_at", "in_default", "computed_at",
]


def _yn(v: str | None) -> bool | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    return (True if s in ("yes", "true", "t", "y")
            else False if s in ("no", "false", "f", "n") else None)


def _f(v) -> float | None:
    return float(v) if v is not None else None


def build_rows(today: date | None = None) -> list[dict]:
    """Compute one analytics row per listed bond (pure; no writes)."""
    today = today or date.today()
    computed_at = datetime.now(UTC)
    core = queries.screener_core()
    cashflows = queries.all_cashflows()
    terms_map = queries.all_terms()
    current = queries.current_ratings()
    history = queries.rating_history()
    defaulted = queries.defaulted_isins()

    rows = []
    for isin, r in core.items():
        sched = schedule.build(isin, cashflows.get(isin, []),
                               terms_map.get(isin), today=today)
        cur = current.get(isin, [])
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

        change = ratings.recent_change(history.get(isin, []), today)

        rows.append({
            "isin": isin,
            "issuer_name": r.get("issuer_name"),
            "sector": r.get("sector"),
            "industry": r.get("industry"),
            "on_nse": r["on_nse"],
            "on_bse": r["on_bse"],
            "nse_symbol": r.get("nse_symbol"),
            "nse_series": r.get("nse_series"),
            "bse_scrip_id": r.get("bse_scrip_id"),
            "coupon": coupon,
            "face_value": face,
            "coupon_basis": r.get("coupon_basis"),
            "frequency": sched.frequency,
            "secured": _yn(r.get("secured")),
            "tax_free": _yn(r.get("tax_free")),
            "perpetual": bool(r.get("perpetual")),
            "maturity_date": r.get("redemption_date"),
            "schedule_shape": sched.shape,
            "ltp": price,
            "trade_date": retail["trade_date"] if retail else None,
            "exchange": retail["exchange"] if retail else None,
            "volume": _f(retail and retail.get("volume")),
            "turnover": _f(retail and retail.get("turnover")),
            "num_trades": retail.get("num_trades") if retail else None,
            "otc_price_pct": _f(otc and (otc.get("wap") or otc.get("last_price"))),
            "otc_yield": _f(otc and (otc.get("wap_yield") or otc.get("last_yield"))),
            "otc_trade_date": otc["trade_date"] if otc else None,
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
            "computed_at": computed_at,
        })
    return rows


def refresh() -> int:
    """Recompute and atomically replace bond_analytics. Returns row count."""
    rows = build_rows()
    cols = ", ".join(_COLS)
    placeholders = ", ".join(f"%({c})s" for c in _COLS)
    with db._conn() as conn:
        with conn.transaction():
            conn.execute("DELETE FROM bond_analytics")
            conn.cursor().executemany(
                f"INSERT INTO bond_analytics ({cols}) VALUES ({placeholders})",
                rows)
    return len(rows)


def is_empty() -> bool:
    with db._conn() as conn:
        r = conn.execute("SELECT count(*) AS n FROM bond_analytics").fetchone()
    return (r["n"] if r else 0) == 0
