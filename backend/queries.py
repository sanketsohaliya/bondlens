"""Read-side SQL for the API: explorer core, bond detail, trade history.

Everything here is read-only. The explorer core loads the whole joined
universe in a handful of set-based queries (not per-ISIN loops) and is
cached by the app layer — data only changes when the daily jobs run.
"""
import math
from datetime import date, datetime
from typing import Any, LiteralString
from zoneinfo import ZoneInfo

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


def explorer_core() -> dict[str, dict]:
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

        # Latest retail trade per exchange (turnover breakdown by venue).
        for r in conn.execute("""
            SELECT DISTINCT ON (isin, exchange) isin, exchange, turnover
            FROM eod_trades
            WHERE segment = 'retail'
            ORDER BY isin, exchange, trade_date DESC
        """).fetchall():
            if r["isin"] in core:
                core[r["isin"]].setdefault("retail_exch", {})[
                    (r["exchange"] or "").upper()] = dict(r)

        # Retail turnover and close on each bond's previous trading day
        # (turnover summed across venues; close from the day's biggest-
        # turnover row) for the explorer's up/down markers.
        for r in conn.execute("""
            WITH daily AS (
                SELECT isin, trade_date, SUM(turnover) AS turnover,
                       (ARRAY_AGG(COALESCE(close, last_price)
                                  ORDER BY turnover DESC NULLS LAST))[1] AS close
                FROM eod_trades
                WHERE segment = 'retail'
                GROUP BY isin, trade_date
            ), ranked AS (
                SELECT isin, turnover, close,
                       ROW_NUMBER() OVER (PARTITION BY isin
                                          ORDER BY trade_date DESC) AS rn
                FROM daily
            )
            SELECT isin, turnover, close FROM ranked WHERE rn = 2
        """).fetchall():
            if r["isin"] in core:
                core[r["isin"]]["prev_turnover"] = r["turnover"]
                core[r["isin"]]["prev_ltp"] = r["close"]
    return core


def all_cashflows() -> dict[str, list[dict]]:
    """bond_cashflows grouped by ISIN (for the schedule engine)."""
    out: dict[str, list[dict]] = {}
    with db._conn() as conn:
        for r in conn.execute(
                "SELECT isin, event, due_date, record_date, amount_payable, "
                "amount_redeem, amount_outstanding FROM bond_cashflows "
                "ORDER BY isin, due_date").fetchall():
            out.setdefault(r["isin"], []).append(r)
    return out


def all_terms() -> dict[str, dict]:
    with db._conn() as conn:
        return {r["isin"]: dict(r) for r in conn.execute(
            "SELECT * FROM bond_terms").fetchall()}


def current_ratings() -> dict[str, list[dict]]:
    """Current agency ratings per ISIN. rating_date carries the event
    date (NSDL ratingChangeDate) — creditRatingDate is only the initial
    assignment date, identical across an agency's rows."""
    out: dict[str, list[dict]] = {}
    with db._conn() as conn:
        for r in conn.execute(
                "SELECT isin, agency, rating, outlook, rating_action, "
                "COALESCE(rating_change_date, rating_date) AS rating_date, "
                "watch, press_release_link AS press_release "
                "FROM bond_ratings "
                "WHERE is_current ORDER BY isin, agency").fetchall():
            out.setdefault(r["isin"], []).append(dict(r))
    return out


def rating_history(isin: str | None = None) -> dict[str, list[dict]]:
    """Full rating trail per ISIN (one ISIN, or everything for the
    explorer's change detection). rating_date carries the event date
    (NSDL ratingChangeDate), falling back to the assignment date for
    initial \"New\" rows that have no change date."""
    out: dict[str, list[dict]] = {}
    q = ("SELECT isin, agency, rating, outlook, rating_action, "
         "COALESCE(rating_change_date, rating_date) AS rating_date, "
         "press_release_link AS press_release, is_current FROM bond_ratings ")
    order = "COALESCE(rating_change_date, rating_date)"
    with db._conn() as conn:
        rows = (conn.execute(q + f"WHERE isin = %s ORDER BY {order}", (isin,))
                if isin else conn.execute(q + f"ORDER BY isin, {order}")
                ).fetchall()
    for r in rows:
        out.setdefault(r["isin"], []).append(dict(r))
    return out


def ratings_for_isins(isins: list[str], limit: int = 25) -> list[dict]:
    """Most recent rating events across a set of ISINs (portfolio news).
    Undated rows are useless as news and are skipped."""
    if not isins:
        return []
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT r.isin, a.issuer_name, r.agency, r.rating, "
            "       r.rating_action AS action, "
            "       COALESCE(r.rating_change_date, r.rating_date) AS rating_date, "
            "       r.press_release_link AS press_release "
            "FROM bond_ratings r "
            "LEFT JOIN bond_analytics a ON a.isin = r.isin "
            "WHERE r.isin = ANY(%s) "
            "  AND COALESCE(r.rating_change_date, r.rating_date) IS NOT NULL "
            "ORDER BY COALESCE(r.rating_change_date, r.rating_date) DESC "
            "LIMIT %s",
            (isins, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def defaulted_isins() -> set:
    """ISINs with a real payment-default record. Technical delays (investor
    account/IFSC issues, cured in days) are tracked separately — branding
    them defaults hid perfectly sound bonds from the explorer."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT isin FROM bond_defaults").fetchall()
    return {r["isin"] for r in rows}


def tech_delay_isins() -> set:
    """ISINs with an NSDL technical-delay record (soft warning only)."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT isin FROM bond_technical_defaults").fetchall()
    return {r["isin"] for r in rows}


# --- Explorer over the precomputed bond_analytics table ---------------------

_EXPLORER_SELECT = """
SELECT a.*,
       CASE WHEN a.maturity_date > CURRENT_DATE
            THEN round((a.maturity_date - CURRENT_DATE) / 365.0, 2)
       END                                        AS years_to_maturity,
       CASE WHEN a.trade_date IS NOT NULL
            THEN CURRENT_DATE - a.trade_date END  AS days_since_traded,
       (a.trade_date IS NULL
        OR CURRENT_DATE - a.trade_date > %(stale_days)s) AS stale,
       CASE WHEN a.otc_trade_date IS NOT NULL
            THEN CURRENT_DATE - a.otc_trade_date END AS otc_days_since_traded
FROM bond_analytics a
"""


def payout_fields(row: dict, today: date) -> dict:
    as_of = row.get("payout_as_of")
    state = row.get("payout_state_snapshot")
    amount = row.get("payout_total_snapshot")
    if as_of is None:
        state = "unavailable"
    elif as_of != today:
        state = "stale"
    elif state not in {"known", "estimated", "incomplete", "open_ended", "unavailable"}:
        state = "unavailable"
    elif state in {"known", "estimated"} and (
        not isinstance(amount, (int, float)) or isinstance(amount, bool) or not math.isfinite(amount) or amount < 0
    ):
        state = "incomplete"
    return {"payout_total_per_unit": amount if state in {"known", "estimated"} else None,
            "payout_total_state": state, "payout_as_of": as_of}


def explorer(f: dict, stale_days: int) -> tuple[list[dict], int, float | None]:
    """Filtered explorer rows from bond_analytics.

    ``f`` mirrors the API query params. Returns (page, total, as_of_epoch).
    """
    conds: list[LiteralString] = []
    p: dict[str, Any] = {"stale_days": stale_days}

    def add(cond: LiteralString, **params: Any):
        conds.append(cond)
        p.update(params)

    # Nothing is ever hidden — defaulted bonds carry an in_default flag
    # the UI renders as a warning. An explicit ISIN list is the user's
    # own watchlist/portfolio.
    if f.get("isins"):
        add("a.isin = ANY(%(isins)s)", isins=list(f["isins"]))
    if f.get("min_ytm") is not None:
        add("a.ytm >= %(min_ytm)s", min_ytm=f["min_ytm"])
    if f.get("max_ytm") is not None:
        add("a.ytm <= %(max_ytm)s", max_ytm=f["max_ytm"])
    if f.get("rating"):
        add("a.credit_rating ILIKE %(rating)s", rating=f"%{f['rating']}%")
    if f.get("min_rating_rank") is not None:
        add("a.rating_rank <= %(rank)s", rank=f["min_rating_rank"])
    if f.get("sector"):
        add("a.sector ILIKE %(sector)s", sector=f"%{f['sector']}%")
    if f.get("industry"):
        add("a.industry ILIKE %(industry)s", industry=f"%{f['industry']}%")
    if f.get("issuer"):
        add("a.issuer_name ILIKE %(issuer)s", issuer=f"%{f['issuer']}%")
    if (f.get("exchange") or "").upper() == "NSE":
        add("a.on_nse")
    elif (f.get("exchange") or "").upper() == "BSE":
        add("a.on_bse")
    if f.get("frequency"):
        add("a.frequency = %(freq)s", freq=f["frequency"].lower())
    # Payout structure — same vocabulary as the explorer's Payout column.
    if f.get("payout") == "bullet":
        add("a.schedule_shape = 'plain'")
    elif f.get("payout") == "staggered":
        add("a.schedule_shape = 'staggered'")
    elif f.get("payout") == "perpetual":
        add("a.schedule_shape = 'perpetual'")
    elif f.get("payout") == "maturity":
        add("a.schedule_shape IN ('zero_coupon', 'cumulative')")
    if f.get("min_years") is not None:
        add("a.maturity_date > CURRENT_DATE AND "
            "round((a.maturity_date - CURRENT_DATE) / 365.0, 2) >= %(min_years)s",
            min_years=f["min_years"])
    if f.get("max_years") is not None:
        add("a.maturity_date > CURRENT_DATE AND "
            "round((a.maturity_date - CURRENT_DATE) / 365.0, 2) <= %(max_years)s",
            max_years=f["max_years"])
    if f.get("max_ltp") is not None:
        add("a.ltp <= %(max_ltp)s", max_ltp=f["max_ltp"])
    if f.get("min_turnover") is not None:
        add("a.turnover >= %(min_turnover)s", min_turnover=f["min_turnover"])
    if f.get("min_otc_turnover") is not None:
        add("a.otc_turnover >= %(min_otc_turnover)s",
            min_otc_turnover=f["min_otc_turnover"])
    if f.get("retail_only"):
        add("a.trade_date IS NOT NULL")
    if f.get("otc_only"):
        add("a.otc_trade_date IS NOT NULL")
    if f.get("secured_only"):
        add("a.secured")
    if f.get("unsecured_only"):
        add("a.secured IS NOT TRUE")
    if f.get("tax_free_only"):
        add("a.tax_free")
    if f.get("below_par"):
        add("a.ltp < a.face_value")
    if f.get("fresh_only"):
        add("a.trade_date IS NOT NULL AND "
            "CURRENT_DATE - a.trade_date <= %(stale_days)s")
    if f.get("traded_within") is not None:
        # N counts trading sessions, not calendar days (weekends and
        # holidays never empty the view): keep bonds whose last trade
        # falls within the N most recent distinct trade dates.
        add("a.trade_date IS NOT NULL AND a.trade_date >= ("
            "SELECT min(d) FROM ("
            "SELECT DISTINCT trade_date AS d FROM bond_analytics "
            "WHERE trade_date IS NOT NULL "
            "ORDER BY d DESC LIMIT %(traded_within)s) recent)",
            traded_within=f["traded_within"])
    if f.get("exact_only"):
        add("a.ytm_method = 'exact'")
    if f.get("downgraded_only"):
        add("a.rating_change = 'down'")
    if f.get("q"):
        add("(a.isin ILIKE %(q)s OR "
            "coalesce(a.nse_symbol, '') || ' ' || "
            "coalesce(a.bse_scrip_id, '') || ' ' || "
            "coalesce(a.issuer_name, '') ILIKE %(q)s)",
            q=f"%{f['q'].strip()}%")

    # The UI sorts a paged result, so the page must be cut in that same
    # order — sorting 1000 rows client-side hides whatever the default
    # turnover ordering pushed past the limit (e.g. untraded D-rated paper).
    sortable: dict[str, LiteralString] = {
        "ytm": "a.ytm",
        "ltp": "a.ltp",
        "turnover": "coalesce(a.turnover, 0)",
        "rating_rank": "a.rating_rank",
        "years_to_maturity": "a.maturity_date",
        "days_since_traded": "a.trade_date",
    }
    sort_col = sortable.get(f.get("sort") or "", "coalesce(a.turnover, 0)")
    sort_dir: LiteralString = "ASC" if f.get("dir") == "asc" else "DESC"
    if f.get("sort") == "days_since_traded":
        # days since = inverse of trade_date: recent-first means date DESC.
        sort_dir = "DESC" if sort_dir == "ASC" else "ASC"

    where: LiteralString = ("WHERE " + " AND ".join(conds)) if conds else ""
    with db._conn() as conn:
        total = conn.execute(
            f"SELECT count(*) AS n FROM bond_analytics a {where}", p).fetchone()
        rows = conn.execute(
            f"{_EXPLORER_SELECT} {where} "
            f"ORDER BY {sort_col} {sort_dir} NULLS LAST, a.isin "
            "LIMIT %(limit)s OFFSET %(offset)s",
            {**p, "limit": f.get("limit", 500), "offset": f.get("offset", 0)},
        ).fetchall()
        as_of = conn.execute(
            "SELECT extract(epoch FROM max(computed_at)) AS t "
            "FROM bond_analytics").fetchone()
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    return ([{**dict(r), **payout_fields(r, today)} for r in rows], total["n"] if total else 0,
            float(as_of["t"]) if as_of and as_of["t"] else None)


def analytics_row(isin: str, stale_days: int) -> dict | None:
    with db._conn() as conn:
        r = conn.execute(f"{_EXPLORER_SELECT} WHERE a.isin = %(isin)s",
                         {"isin": isin, "stale_days": stale_days}).fetchone()
    return {**dict(r), **payout_fields(r, datetime.now(ZoneInfo("Asia/Kolkata")).date())} if r else None


def explorer_filters() -> dict:
    with db._conn() as conn:
        sectors = conn.execute(
            "SELECT DISTINCT sector FROM bond_analytics "
            "WHERE sector IS NOT NULL ORDER BY sector").fetchall()
        freqs = conn.execute(
            "SELECT DISTINCT frequency FROM bond_analytics "
            "WHERE frequency IS NOT NULL").fetchall()
    # Fixed, judgement-based order: the common payment rhythms first,
    # then the accumulating/odd shapes.
    canon = ["monthly", "annual", "quarterly", "semiannual",
             "cumulative", "irregular"]
    present = {r["frequency"] for r in freqs}
    frequencies = ([f for f in canon if f in present]
                   + sorted(present - set(canon)))
    return {"sectors": [r["sector"] for r in sectors],
            "frequencies": frequencies}


def symbol_bond_map() -> dict[tuple[str, str], dict]:
    """(exchange, Kite tradingsymbol) -> {isin, issuer_name} for every
    listed bond, using Kite's symbol conventions (NSE bonds trade as
    ``{SYMBOL}-{SERIES}``, BSE as the scrip id). Used to resolve the
    user's Kite orders back to bonds."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT isin, issuer_name, nse_symbol, nse_series, bse_scrip_id "
            "FROM bond_analytics "
            "WHERE nse_symbol IS NOT NULL OR bse_scrip_id IS NOT NULL"
        ).fetchall()
    out: dict[tuple[str, str], dict] = {}
    for r in rows:
        bond = {"isin": r["isin"], "issuer_name": r["issuer_name"]}
        if r["nse_symbol"]:
            if r["nse_series"]:
                out[("NSE", f"{r['nse_symbol']}-{r['nse_series']}")] = bond
            out.setdefault(("NSE", r["nse_symbol"]), bond)
        if r["bse_scrip_id"]:
            out[("BSE", r["bse_scrip_id"])] = bond
    return out


# --- Detail view ------------------------------------------------------------

def cashflow_evidence_inputs(isin: str) -> dict | None:
    sources = ("nse_debt_announcements", "bse_corporate_actions")
    with db._conn() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        bond = conn.execute("SELECT isin, status, on_nse, on_bse FROM bonds WHERE isin = %s", (isin,)).fetchone()
        if bond is None:
            return None
        terms = conn.execute("SELECT * FROM bond_terms WHERE isin = %s", (isin,)).fetchone()
        flows = conn.execute(
            "SELECT event, due_date, record_date, payment_date, amount_payable, amount_redeem, amount_outstanding "
            "FROM bond_cashflows WHERE isin = %s ORDER BY due_date", (isin,),
        ).fetchall()
        tables = conn.execute("SELECT to_regclass('corporate_actions') IS NOT NULL AS available").fetchone()
        records: list[dict] = []
        targets: dict[str, dict] = {}
        latest: dict[str, dict] = {}
        success: dict[str, Any] = {}
        if tables and tables["available"]:
            records = conn.execute(
                "SELECT a.id, a.raw_id, a.source, a.source_record_id, a.scrip_code, a.issuer_name, "
                "a.category_raw, a.headline, a.announced_at, a.first_seen_at, a.last_seen_at, "
                "a.attachment_urls, a.source_url, a.raw, "
                "EXISTS (SELECT 1 FROM corporate_action_isins v WHERE v.action_id=a.id "
                "AND v.isin=%s AND v.method='bse_master') AS verified_bse "
                "FROM corporate_actions a WHERE EXISTS (SELECT 1 FROM corporate_action_isins i "
                "WHERE i.action_id=a.id AND i.isin=%s) ORDER BY a.id LIMIT 5001", (isin, isin),
            ).fetchall()
            targets = {r["source"]: dict(r) for r in conn.execute(
                "SELECT source, active, state, blocked_reason, history_from, pending, recent_pending "
                "FROM corporate_action_targets WHERE source = ANY(%s) AND isin=%s", (list(sources), isin),
            )}
            latest = {r["source"]: dict(r) for r in conn.execute(
                "SELECT DISTINCT ON (source) source, ok FROM corporate_action_runs "
                "WHERE mode='recent' ORDER BY source, run_at DESC, id DESC",
            )}
            success = {r["source"]: r["last_success_at"] for r in conn.execute(
                "SELECT source, max(finished_at) AS last_success_at FROM corporate_action_runs "
                "WHERE mode='recent' AND ok IS TRUE GROUP BY source",
            )}
        return {"bond": dict(bond), "terms": dict(terms) if terms else None,
                "cashflows": [dict(r) for r in flows], "records": [dict(r) for r in records[:5000]],
                "partial": len(records) > 5000,
                "availability": [{"source": source, "target": targets.get(source),
                                  "last_success_at": success.get(source),
                                  "latest_ok": latest.get(source, {}).get("ok")} for source in sources]}


def bond_row(isin: str) -> dict | None:
    with db._conn() as conn:
        r = conn.execute("""
            SELECT b.*, COALESCE(i.issuer_name, b.issuer_name) AS issuer_name,
                   i.sector, i.industry, i.basic_industry, i.macro,
                   i.cin, i.lei, i.sec_type_desc, i.instrument_type
            FROM bonds b LEFT JOIN bond_info i ON i.isin = b.isin
            WHERE b.isin = %s
        """, (isin,)).fetchone()
    return dict(r) if r else None


def bond_terms(isin: str) -> dict | None:
    with db._conn() as conn:
        r = conn.execute(
            "SELECT * FROM bond_terms WHERE isin = %s", (isin,)).fetchone()
    return dict(r) if r else None


def bonds_symbols(isins: list[str]) -> dict[str, dict]:
    """isin -> {nse_symbol, nse_series, bse_scrip_id} for a set of bonds
    (the fields needed to resolve Kite tradingsymbols), in one query."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT isin, nse_symbol, nse_series, bse_scrip_id "
            "FROM bond_analytics WHERE isin = ANY(%s)", (isins,)).fetchall()
    return {r["isin"]: dict(r) for r in rows}


def terms_for(isins: list[str]) -> dict[str, dict]:
    """bond_terms rows keyed by ISIN, in one query (bulk bond_terms)."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM bond_terms WHERE isin = ANY(%s)",
            (isins,)).fetchall()
    return {r["isin"]: dict(r) for r in rows}


def cashflows_for(isins: list[str]) -> dict[str, list[dict]]:
    """bond_cashflows grouped by ISIN, in one query (bulk bond_cashflows)."""
    out: dict[str, list[dict]] = {}
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT isin, event, due_date, record_date, payment_date, "
            "amount_payable, amount_redeem, amount_outstanding, "
            "actual_payment_date, reason_delay_payment "
            "FROM bond_cashflows WHERE isin = ANY(%s) ORDER BY isin, due_date",
            (isins,)).fetchall()
    for r in rows:
        out.setdefault(r["isin"], []).append(dict(r))
    return out


def bond_trustee(isin: str) -> str | None:
    with db._conn() as conn:
        r = conn.execute(
            "SELECT trustee_name FROM bond_contacts WHERE isin = %s",
            (isin,)).fetchone()
    return r["trustee_name"] if r else None


def bond_cashflows(isin: str) -> list[dict]:
    with db._conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT event, due_date, record_date, payment_date, "
            "amount_payable, amount_redeem, amount_outstanding, "
            "actual_payment_date, reason_delay_payment "
            "FROM bond_cashflows WHERE isin = %s ORDER BY due_date",
            (isin,)).fetchall()]


def bond_extras(isin: str) -> dict[str, list[dict]]:
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


def issuer_siblings(isin: str, issuer_name: str) -> list[dict]:
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


def trades(isin: str, date_from: date | None = None,
           date_to: date | None = None, exchange: str | None = None,
           segment: str | None = None) -> list[dict]:
    """Full trade history for one ISIN (charts)."""
    conds: list[LiteralString] = ["isin = %s"]
    params: list[Any] = [isin]
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

def status() -> dict:
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
