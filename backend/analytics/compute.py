"""Precompute per-bond explorer analytics into the bond_analytics table.

Runs inside the jobs container after the NSDL and EOD parsers (the only
events that change the inputs). The API then serves the explorer as a
plain indexed SELECT — no in-process cache, no cold start.

Date-relative fields (staleness, years to maturity) are intentionally NOT
stored: they drift daily and are derived in SQL at read time. YTM is
price-dependent and refreshed here whenever new trades land, which is
exactly as often as prices can change.
"""
import math
from datetime import UTC, date, datetime
from typing import LiteralString

from psycopg.types.json import Jsonb

from .. import db, queries
from . import ratings, schedule, valuation, yields

STALE_AFTER_DAYS = 7  # retail price older than this is flagged stale

# Table DDL lives in migrations/ (0001 baseline).
_COLS: list[LiteralString] = [
    "isin", "issuer_name", "sector", "industry", "on_nse", "on_bse",
    "nse_symbol", "nse_series", "bse_scrip_id", "coupon", "face_value",
    "coupon_basis", "frequency", "secured", "tax_free", "perpetual",
    "maturity_date", "schedule_shape", "future_flows", "total_flows",
    "payout_total_snapshot", "payout_state_snapshot", "payout_as_of", "payout_valid_until",
    "ltp", "trade_date", "exchange",
    "volume", "turnover", "num_trades", "nse_turnover", "bse_turnover",
    "prev_turnover", "prev_ltp",
    "otc_price_pct", "otc_yield",
    "otc_trade_date", "otc_turnover", "ytm", "ytm_method", "ytm_reliable", "valuation",
    "current_yield", "duration",
    "credit_rating", "rating_rank", "rating_change", "rating_prev",
    "rating_changed_at", "in_default", "tech_delay", "computed_at",
]


def _yn(v: str | None) -> bool | None:
    """NSDL boolean-ish text → bool. Covers yes/no AND the secured
    column's \"Secured\"/\"Unsecured\" values (exact tokens — substring
    matching would let \"unsecured\" hit \"secured\")."""
    if v is None:
        return None
    s = str(v).strip().lower()
    return (True if s in ("yes", "true", "t", "y", "secured")
            else False if s in ("no", "false", "f", "n", "unsecured")
            else None)


# These ranges prompt review, not a verdict that the calculation is wrong.
# Near payment dates, small price gaps can produce extreme annualization.
_RELIABLE = valuation.RELIABLE
_RELIABLE_NEAR = valuation.RELIABLE_NEAR
_NEAR_DAYS = valuation.NEAR_DAYS
# OTC comparisons are contextual: dates, prices and quotation conventions
# may differ. Only bounded, non-future prints participate in the comparison.
_OTC_CHECK_MAX_GAP = valuation.OTC_MAX_GAP
_OTC_CHECK_MAX_AGE_DAYS = valuation.OTC_MAX_AGE


def ytm_reliability(ytm: float | None, maturity: date | None,
                    today: date, coupon_basis: str | None = None,
                    otc_yield: float | None = None,
                    otc_trade_date: date | None = None) -> bool | None:
    """False = display with caution; None = no YTM to judge."""
    if ytm is None:
        return None
    # Floating / market-linked coupons: published future flows assume the
    # current rate holds, so any YTM is indicative at best.
    # Cross-check against a fresh institutional OTC print.
    return all(w["severity"] == "info" for w in valuation.cautions(
        ytm, maturity, today, coupon_basis, otc_yield, otc_trade_date))


def _f(v) -> float | None:
    return float(v) if v is not None else None


def payout_summary(isin: str, cashflows: list[dict], terms: dict | None, today: date) -> dict:
    terms = terms or {}
    sched = schedule.build(isin, cashflows, terms, today=today)
    eligible = [f for f in sched.flows if f.receivable_by_buyer(today)]
    # The buyer total only changes when `today` reaches a flow's effective
    # (payment/due) date or record date — the two dates Flow.receivable_by_buyer
    # tests. Record the earliest such date after today so the read path can
    # keep serving this snapshot until then instead of blanking at midnight.
    # Keep in step with receivable_by_buyer if that predicate ever changes.
    boundaries = [d for f in sched.flows for d in (f.effective_date, f.record_date)
                  if d is not None and today < d and d.year < 2500]
    result = {"payout_total_snapshot": None, "payout_state_snapshot": "unavailable", "payout_as_of": today,
              "payout_valid_until": min(boundaries) if boundaries else None,
              "future_flows": len(sched.future_flows(today)), "total_flows": len(sched.flows)}
    maturity = terms.get("redemption_date")
    if terms.get("perpetual") or sched.shape == "perpetual" or (maturity and maturity.year >= 2500):
        result["payout_state_snapshot"] = "open_ended"
    elif sched.flows:
        invalid_source = any(v is not None and (not math.isfinite(float(v)) or float(v) < 0)
                             for r in cashflows for key in ("amount_payable", "amount_redeem") if (v := r.get(key)) is not None)
        incomplete = (not sched.usable or invalid_source or any(r.get("due_date") is None for r in cashflows)
                      or bool(sched.pricing_issues(today, check_convention=False))
                      or any(f.due_date.year >= 2500 or f.effective_date.year >= 2500
                             or (f.record_date is not None and f.record_date.year >= 2500)
                             or f.amount is None or not math.isfinite(f.amount) or f.amount < 0 for f in eligible))
        if incomplete:
            result["payout_state_snapshot"] = "incomplete"
        else:
            amount = sum(f.amount for f in eligible if f.amount is not None)
            if not math.isfinite(amount):
                result["payout_state_snapshot"] = "incomplete"
            else:
                variable = (str(terms.get("coupon_basis") or "").strip().lower().startswith("variable")
                            or "float" in str(terms.get("coupon_type") or "").lower())
                estimated = any(f.estimated or (variable and f.event == "interest") for f in eligible)
                result.update(payout_total_snapshot=round(amount, 2),
                              payout_state_snapshot="estimated" if estimated else "known")
    return result


def build_rows(today: date | None = None) -> list[dict]:
    """Compute one analytics row per listed bond (pure; no writes)."""
    today = today or date.today()
    computed_at = datetime.now(UTC)
    core = queries.explorer_core()
    cashflows = queries.all_cashflows()
    terms_map = queries.all_terms()
    payout_cashflows = queries.cashflows_for(list(core)) if core else {}
    current = queries.current_ratings()
    history = queries.rating_history()
    defaulted = queries.defaulted_isins()
    tech_delays = queries.tech_delay_isins()

    rows = []
    for isin, r in core.items():
        sched = schedule.build(isin, cashflows.get(isin, []),
                               terms_map.get(isin), today=today)
        payout = payout_summary(isin, payout_cashflows.get(isin, []), terms_map.get(isin), today)
        cur = current.get(isin, [])
        retail = r.get("retail_trade")
        otc = r.get("otc_trade")

        price = retail and (retail.get("close") or retail.get("last_price"))
        price = float(price) if price else None
        coupon = float(r["coupon_rate"]) if r.get("coupon_rate") else None
        face = float(r["face_value"]) if r.get("face_value") else None

        red_date = r.get("redemption_date")
        perpetual = bool(r.get("perpetual")) or (
            red_date is not None and red_date.year >= 2500)
        if perpetual:
            red_date = None  # the 9999 sentinel is not a maturity

        # Maturity-based YTM is meaningless for perpetuals; price to
        # the earliest call when one is published.
        priced = valuation.calculate(price, sched, terms_map.get(isin), today, allow_approx=True,
                                     otc_yield=_f(otc and (otc.get("wap_yield") or otc.get("last_yield"))),
                                     otc_trade_date=otc["trade_date"] if otc else None,
                                     price_date=retail["trade_date"] if retail else None)
        ytm, ytm_method = priced["ytm"], priced["ytm_method"]

        change = ratings.latest_change(history.get(isin, []))

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
            "perpetual": perpetual,
            "maturity_date": red_date,
            "schedule_shape": sched.shape,
            **payout,
            "ltp": price,
            "trade_date": retail["trade_date"] if retail else None,
            "exchange": retail["exchange"] if retail else None,
            "volume": _f(retail and retail.get("volume")),
            "turnover": _f(retail and retail.get("turnover")),
            "num_trades": retail.get("num_trades") if retail else None,
            "nse_turnover": _f(
                (r.get("retail_exch") or {}).get("NSE", {}).get("turnover")),
            "bse_turnover": _f(
                (r.get("retail_exch") or {}).get("BSE", {}).get("turnover")),
            "prev_turnover": _f(r.get("prev_turnover")),
            "prev_ltp": _f(r.get("prev_ltp")),
            "otc_price_pct": _f(otc and (otc.get("wap") or otc.get("last_price"))),
            "otc_yield": _f(otc and (otc.get("wap_yield") or otc.get("last_yield"))),
            "otc_trade_date": otc["trade_date"] if otc else None,
            "otc_turnover": _f(otc and otc.get("turnover")),
            "ytm": ytm,
            "ytm_method": ytm_method,
            "ytm_reliable": priced["ytm_reliable"],
            "valuation": priced,
            "current_yield": yields.current_yield(price, coupon, face),
            "duration": yields.modified_duration(price, sched, ytm, today)
                        if ytm_method in ("exact", "estimated") else None,
            # Explorer shows (and filters on) the most recent agency action
            # only; the full multi-agency picture lives in the drawer timeline.
            "credit_rating": ratings.composite(ratings.latest(cur)),
            "rating_rank": ratings.best_rank(ratings.latest(cur)),
            "rating_change": change["direction"] if change else None,
            "rating_prev": change["prev_rating"] if change else None,
            "rating_changed_at": change["rating_date"] if change else None,
            "in_default": isin in defaulted or ratings.in_default(cur),
            "tech_delay": isin in tech_delays,
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
                [{**row, "valuation": Jsonb(row["valuation"])} for row in rows])
    return len(rows)


def is_empty() -> bool:
    with db._conn() as conn:
        r = conn.execute("SELECT count(*) AS n FROM bond_analytics").fetchone()
    return (r["n"] if r else 0) == 0
