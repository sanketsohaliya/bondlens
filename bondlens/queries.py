"""Read-side SQL for the API: screener core, bond detail, trade history.

Everything here is read-only. The screener core loads the whole joined
universe in a handful of set-based queries (not per-ISIN loops) and is
cached by the app layer — data only changes when the daily jobs run.
"""
from datetime import date
from typing import Dict, List, Optional

from . import db

# Latest retail/OTC trade per ISIN. eod_trades PK (isin, trade_date,
# exchange, segment) serves DISTINCT ON (isin) ... ORDER BY isin,
# trade_date DESC directly.
_LATEST_TRADE_SQL = """
SELECT DISTINCT ON (isin) isin, trade_date, exchange,
       close, last_price, wap, last_yield, wap_yield,
       volume, turnover, num_trades
FROM eod_trades
WHERE segment = %s
ORDER BY isin, trade_date DESC, turnover DESC NULLS LAST
"""


def screener_core() -> Dict[str, Dict]:
    """One dict per listed bond with identity, terms, info and the latest
    retail + OTC trades. Keyed by ISIN. Analytics are layered on top by
    the app (schedules need the cashflow table separately)."""
    with db._conn() as conn:
        rows = conn.execute("""
            SELECT b.isin, b.status, b.on_nse, b.on_bse, b.bse_status,
                   COALESCE(i.issuer_name, b.issuer_name) AS issuer_name,
                   b.nse_symbol, b.nse_series, b.bse_scrip_code, b.bse_scrip_id,
                   i.sector, i.industry, i.basic_industry,
                   t.face_value, t.coupon_rate, t.coupon_basis, t.coupon_type,
                   t.interest_payment_frequency, t.day_count_convention,
                   t.secured, t.tax_free, t.perpetual,
                   t.allotment_date, t.redemption_date,
                   t.redemption_type, t.call_indicator, t.put_indicator,
                   t.category, t.instrument_desc
            FROM bonds b
            LEFT JOIN bond_info  i ON i.isin = b.isin
            LEFT JOIN bond_terms t ON t.isin = b.isin
            WHERE b.status = 'listed'
        """).fetchall()
        core = {r["isin"]: dict(r) for r in rows}

        for segment, key in (("retail", "retail_trade"), ("otc", "otc_trade")):
            for r in conn.execute(_LATEST_TRADE_SQL, (segment,)).fetchall():
                if r["isin"] in core:
                    core[r["isin"]][key] = dict(r)
    return core


def all_cashflows() -> Dict[str, List[Dict]]:
    """bond_cashflows grouped by ISIN (for the schedule engine)."""
    out: Dict[str, List[Dict]] = {}
    with db._conn() as conn:
        for r in conn.execute(
                "SELECT isin, event, due_date, record_date, amount_payable, "
                "amount_redeem, amount_outstanding FROM bond_cashflows "
                "ORDER BY isin, due_date").fetchall():
            out.setdefault(r["isin"], []).append(r)
    return out


def all_terms() -> Dict[str, Dict]:
    with db._conn() as conn:
        return {r["isin"]: dict(r) for r in conn.execute(
            "SELECT * FROM bond_terms").fetchall()}


def current_ratings() -> Dict[str, List[Dict]]:
    """Current agency ratings per ISIN."""
    out: Dict[str, List[Dict]] = {}
    with db._conn() as conn:
        for r in conn.execute(
                "SELECT isin, agency, rating, outlook, rating_action, "
                "rating_date, watch, press_release_link AS press_release "
                "FROM bond_ratings "
                "WHERE is_current ORDER BY isin, agency").fetchall():
            out.setdefault(r["isin"], []).append(dict(r))
    return out


def rating_history(isin: Optional[str] = None) -> Dict[str, List[Dict]]:
    """Full rating trail per ISIN (one ISIN, or everything for the
    screener's change detection)."""
    out: Dict[str, List[Dict]] = {}
    q = ("SELECT isin, agency, rating, outlook, rating_action, rating_date, "
         "press_release_link AS press_release, is_current FROM bond_ratings ")
    with db._conn() as conn:
        rows = (conn.execute(q + "WHERE isin = %s ORDER BY rating_date", (isin,))
                if isin else conn.execute(q + "ORDER BY isin, rating_date")
                ).fetchall()
    for r in rows:
        out.setdefault(r["isin"], []).append(dict(r))
    return out


def defaulted_isins() -> set:
    """ISINs with any payment-default record."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT isin FROM bond_defaults "
            "UNION SELECT DISTINCT isin FROM bond_technical_defaults"
        ).fetchall()
    return {r["isin"] for r in rows}


# --- Detail view ------------------------------------------------------------

def bond_row(isin: str) -> Optional[Dict]:
    with db._conn() as conn:
        r = conn.execute("""
            SELECT b.*, COALESCE(i.issuer_name, b.issuer_name) AS issuer_name,
                   i.sector, i.industry, i.basic_industry, i.macro,
                   i.cin, i.lei
            FROM bonds b LEFT JOIN bond_info i ON i.isin = b.isin
            WHERE b.isin = %s
        """, (isin,)).fetchone()
    return dict(r) if r else None


def bond_terms(isin: str) -> Optional[Dict]:
    with db._conn() as conn:
        r = conn.execute(
            "SELECT * FROM bond_terms WHERE isin = %s", (isin,)).fetchone()
    return dict(r) if r else None


def bond_cashflows(isin: str) -> List[Dict]:
    with db._conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT event, due_date, record_date, payment_date, "
            "amount_payable, amount_redeem, amount_outstanding, "
            "actual_payment_date, reason_delay_payment "
            "FROM bond_cashflows WHERE isin = %s ORDER BY due_date",
            (isin,)).fetchall()]


def bond_extras(isin: str) -> Dict[str, List[Dict]]:
    """Listings, documents, further issues, redemption events, defaults."""
    out = {}
    with db._conn() as conn:
        out["listings"] = [dict(r) for r in conn.execute(
            "SELECT exchange, issue_type, listing_date, allotment_date, "
            "quantity, term_sheet_link FROM bond_listings "
            "WHERE isin = %s ORDER BY listing_date DESC NULLS LAST",
            (isin,)).fetchall()]
        out["documents"] = [dict(r) for r in conn.execute(
            "SELECT doc_name, source, download_link FROM bond_documents "
            "WHERE isin = %s", (isin,)).fetchall()]
        out["further_issues"] = [dict(r) for r in conn.execute(
            "SELECT allotment_date, allotment_quantity, issue_price, "
            "issue_size FROM bond_further_issues "
            "WHERE isin = %s ORDER BY allotment_date", (isin,)).fetchall()]
        out["redemption_events"] = [dict(r) for r in conn.execute(
            "SELECT redemption_date, value_redeemed, quantity_redeemed, "
            "method, reason FROM bond_redemption_events "
            "WHERE isin = %s ORDER BY redemption_date", (isin,)).fetchall()]
        out["defaults"] = [dict(r) for r in conn.execute(
            "SELECT issue_nature, issue_size, interest_due_date, "
            "redemption_due_date, default_details, actual_payment_details "
            "FROM bond_defaults WHERE isin = %s", (isin,)).fetchall()]
        out["technical_defaults"] = [dict(r) for r in conn.execute(
            "SELECT agency, payment_due_date, amount_to_be_paid, amount_paid, "
            "failure_reason, update_date, press_release_link "
            "FROM bond_technical_defaults WHERE isin = %s "
            "ORDER BY payment_due_date", (isin,)).fetchall()]
    return out


def issuer_siblings(isin: str, issuer_name: str) -> List[Dict]:
    """Other listed bonds by the same issuer, with latest retail price."""
    with db._conn() as conn:
        return [dict(r) for r in conn.execute("""
            WITH sib AS (
                SELECT b.isin, b.nse_symbol, b.nse_series,
                       t.coupon_rate, t.face_value, t.redemption_date
                FROM bonds b
                LEFT JOIN bond_info  i ON i.isin = b.isin
                LEFT JOIN bond_terms t ON t.isin = b.isin
                WHERE b.status = 'listed' AND b.isin <> %s
                  AND COALESCE(i.issuer_name, b.issuer_name) = %s
            )
            SELECT sib.*, lt.trade_date, lt.close, lt.turnover
            FROM sib
            LEFT JOIN LATERAL (
                SELECT trade_date, close, turnover FROM eod_trades e
                WHERE e.isin = sib.isin AND e.segment = 'retail'
                ORDER BY trade_date DESC LIMIT 1
            ) lt ON true
            ORDER BY sib.redemption_date NULLS LAST
        """, (isin, issuer_name)).fetchall()]


def trades(isin: str, date_from: Optional[date] = None,
           date_to: Optional[date] = None, exchange: Optional[str] = None,
           segment: Optional[str] = None) -> List[Dict]:
    """Full trade history for one ISIN (charts)."""
    conds, params = ["isin = %s"], [isin]
    if date_from:
        conds.append("trade_date >= %s")
        params.append(date_from)
    if date_to:
        conds.append("trade_date <= %s")
        params.append(date_to)
    if exchange:
        conds.append("exchange = %s")
        params.append(exchange.upper())
    if segment:
        conds.append("segment = %s")
        params.append(segment.lower())
    with db._conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT trade_date, exchange, segment, price_basis, "
            "open, high, low, close, last_price, wap, last_yield, wap_yield, "
            "volume, turnover, num_trades "
            f"FROM eod_trades WHERE {' AND '.join(conds)} "
            "ORDER BY trade_date", params).fetchall()]


# --- Data freshness ---------------------------------------------------------

def status() -> Dict:
    with db._conn() as conn:
        universe = conn.execute(
            "SELECT run_at, ok, total, added, dropped FROM bond_runs "
            "ORDER BY id DESC LIMIT 1").fetchone()
        nsdl = conn.execute(
            "SELECT run_at, finished_at, ok, fetched, failed FROM nsdl_runs "
            "ORDER BY id DESC LIMIT 1").fetchone()
        eod = conn.execute(
            "SELECT source, MAX(trade_date) AS latest FROM eod_raw "
            "WHERE http_status = 200 GROUP BY source").fetchall()
        latest_trade = conn.execute(
            "SELECT MAX(trade_date) AS d FROM eod_trades").fetchone()
        counts = conn.execute(
            "SELECT COUNT(*) FILTER (WHERE status = 'listed') AS listed, "
            "COUNT(*) AS total FROM bonds").fetchone()
    return {
        "universe_run": dict(universe) if universe else None,
        "nsdl_run": dict(nsdl) if nsdl else None,
        "eod_sources": {r["source"]: r["latest"] for r in eod},
        "latest_trade_date": latest_trade["d"] if latest_trade else None,
        "bonds_listed": counts["listed"] if counts else 0,
        "bonds_total": counts["total"] if counts else 0,
    }
