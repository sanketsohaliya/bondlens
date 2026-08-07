"""Portfolio notification generation.

Pure detection functions (unit-tested without a database) plus a thin
generate() that walks every user with portfolio lots and inserts rows
into user_notifications. The (user_id, dedupe_key) unique constraint
makes re-runs idempotent, so the cron can fire twice a day safely
(scripts/generate_notifications.py, after the NSDL and EOD parses).
"""
from datetime import date, timedelta
from statistics import median

from . import db, queries
from .analytics import portfolio as pf
from .analytics import ratings, schedule
from .analytics.compute import STALE_AFTER_DAYS

UPCOMING_DAYS = 7            # heads-up window before a payment
CONFIRM_LOOKBACK_DAYS = 60   # how far back to track past payments
RATING_WINDOW_DAYS = 30      # only recent rating moves alert
DEFAULT_WINDOW_DAYS = 45     # only recent default filings alert
ANOMALY_PCT = 5.0            # deviation from the trailing median that alerts
ANOMALY_WINDOW_DAYS = 30     # trailing price window
ANOMALY_MIN_SESSIONS = 5     # prior sessions needed for a meaningful median
ANOMALY_STALE_DAYS = 3       # latest trade must be this recent to alert


def _inr(v: float | None) -> str:
    return "?" if v is None else f"\u20b9{v:,.0f}"


def _event_name(event: str) -> str:
    return "Redemption" if event == "redemption" else "Coupon"


# --- Pure detectors ---------------------------------------------------------
# Each returns a list of alert dicts:
# {kind, severity, isin, title, body, dedupe_key}

def rating_alerts(isin: str, label: str, history: list[dict],
                  today: date) -> list[dict]:
    """Recent rating movement on a held bond (ratings.latest_change)."""
    chg = ratings.latest_change(history)
    if chg is None or (today - chg["rating_date"]).days > RATING_WINDOW_DAYS:
        return []
    down = chg["direction"] == "down"
    verb = "downgraded" if down else "upgraded"
    return [{
        "kind": "rating",
        "severity": "warn" if down else "info",
        "isin": isin,
        "title": f"{label} {verb} "
                 f"{chg.get('prev_rating') or '?'} \u2192 {chg.get('rating') or '?'}",
        "body": f"{chg['agency']}, {chg['rating_date']:%d %b %Y}",
        "dedupe_key": f"rating:{isin}:{chg['rating_date']}:{chg.get('rating')}",
    }]


def payment_alerts(isin: str, label: str, lots: list[dict],
                   flows: list[schedule.Flow], raw_rows: list[dict],
                   today: date) -> list[dict]:
    """Payment lifecycle on a held bond: upcoming -> due today ->
    confirmed (NSDL actual_payment_date) or issuer-filed delay.

    Holder semantics per lot: a flow only alerts (and its amount only
    counts) for the units bought before its record date — matching
    the portfolio cashflow chart.

    A missing actual_payment_date is NOT treated as overdue: NSDL's
    payment-execution reporting lags or is never filed for many
    issuers, so silence carries no signal. Only an explicit
    reason_delay_payment warns.

    ``flows`` carry normalized per-unit amounts; ``raw_rows`` are the
    bond_cashflows rows carrying actual_payment_date/reason_delay_payment.
    Past flows without a matching raw row (estimated schedules) are
    skipped — no confirmation data exists to judge them.
    """
    raw_by_due: dict[date, dict] = {}
    for r in raw_rows:
        if r.get("due_date") is not None:
            raw_by_due.setdefault(r["due_date"], r)

    alerts: list[dict] = []
    for f in flows:
        units = pf.receiving_units(lots, f)
        if not units:
            continue
        amount = round(f.amount * units, 2) if f.amount else None
        ev = _event_name(f.event)
        amt_txt = f" {_inr(amount)}" if amount is not None else ""
        est_txt = " (estimated)" if f.estimated else ""
        d = f.due_date

        if today < d <= today + timedelta(days=UPCOMING_DAYS):
            alerts.append({
                "kind": "pay_upcoming", "severity": "info", "isin": isin,
                "title": f"{ev}{amt_txt} due {d:%d %b} \u2014 {label}{est_txt}",
                "body": "",
                "dedupe_key": f"pay_upcoming:{isin}:{d}",
            })
        elif d == today:
            alerts.append({
                "kind": "pay_today", "severity": "info", "isin": isin,
                "title": f"Due today: {ev.lower()}{amt_txt} \u2014 {label}{est_txt}",
                "body": "Check that the credit lands in your bank account.",
                "dedupe_key": f"pay_today:{isin}:{d}",
            })
        elif today - timedelta(days=CONFIRM_LOOKBACK_DAYS) <= d < today:
            raw = raw_by_due.get(d)
            if raw is None:
                continue
            paid_on = raw.get("actual_payment_date")
            reason = (raw.get("reason_delay_payment") or "").strip()
            if paid_on is not None:
                late = paid_on > d
                late_txt = f" ({(paid_on - d).days}d late)" if late else ""
                alerts.append({
                    "kind": "pay_confirmed",
                    "severity": "warn" if late else "info", "isin": isin,
                    "title": f"{ev}{amt_txt} paid on "
                             f"{paid_on:%d %b}{late_txt} \u2014 {label}",
                    "body": "Payment execution reported to NSDL.",
                    "dedupe_key": f"pay_confirmed:{isin}:{d}",
                })
            elif reason:
                alerts.append({
                    "kind": "pay_overdue", "severity": "warn", "isin": isin,
                    "title": f"{ev}{amt_txt} due {d:%d %b} delayed \u2014 {label}",
                    "body": f"Issuer-filed reason: {reason}",
                    "dedupe_key": f"pay_overdue:{isin}:{d}",
                })
    return alerts


def default_alerts(isin: str, label: str, defaults: list[dict],
                   tech_defaults: list[dict], today: date) -> list[dict]:
    """New default / technical-delay filings against a held bond."""
    alerts: list[dict] = []
    for r in defaults:
        d = (r.get("verification_date") or r.get("interest_due_date")
             or r.get("redemption_due_date"))
        if d is None or (today - d).days > DEFAULT_WINDOW_DAYS:
            continue
        detail = (r.get("default_details") or r.get("issue_nature") or "").strip()
        alerts.append({
            "kind": "default", "severity": "critical", "isin": isin,
            "title": f"Default filed \u2014 {label}",
            "body": detail,
            "dedupe_key": f"default:{isin}:{d}",
        })
    for r in tech_defaults:
        d = r.get("update_date") or r.get("payment_due_date")
        if d is None or (today - d).days > DEFAULT_WINDOW_DAYS:
            continue
        parts = []
        if r.get("failure_reason"):
            parts.append(str(r["failure_reason"]).strip())
        if r.get("amount_to_be_paid") is not None:
            parts.append(f"due {_inr(float(r['amount_to_be_paid']))}, "
                         f"paid {_inr(float(r.get('amount_paid') or 0))}")
        alerts.append({
            "kind": "default", "severity": "warn", "isin": isin,
            "title": f"Technical payment delay reported \u2014 {label}",
            "body": "; ".join(parts),
            "dedupe_key": f"techdefault:{isin}:{r.get('payment_due_date')}",
        })
    return alerts


def anomaly_alerts(isin: str, label: str, sessions: list[tuple[date, float]],
                   today: date) -> list[dict]:
    """Latest traded price deviating > ANOMALY_PCT from the trailing
    median. ``sessions`` are (trade_date, price) ascending; the last one
    is compared against the median of the rest."""
    if len(sessions) < ANOMALY_MIN_SESSIONS + 1:
        return []
    last_d, last_p = sessions[-1]
    if last_p is None or (today - last_d).days > ANOMALY_STALE_DAYS:
        return []
    prior = [p for _, p in sessions[:-1] if p is not None]
    if len(prior) < ANOMALY_MIN_SESSIONS:
        return []
    med = median(prior)
    if not med:
        return []
    dev = (last_p - med) / med * 100.0
    if abs(dev) < ANOMALY_PCT:
        return []
    drop = dev < 0
    return [{
        "kind": "anomaly",
        "severity": "warn" if drop else "info",
        "isin": isin,
        "title": f"{label} traded {abs(dev):.1f}% "
                 f"{'below' if drop else 'above'} its 30-day median",
        "body": f"Last {_inr(last_p)} on {last_d:%d %b} "
                f"vs median {_inr(med)}",
        "dedupe_key": f"anomaly:{isin}:{last_d}",
    }]


# --- DB-facing generation ---------------------------------------------------

def _price_sessions(isins: list[str], today: date) -> dict[str, list[tuple[date, float]]]:
    """Per-ISIN daily turnover-weighted dirty prices over the anomaly
    window (retail exchange trades only, comparable within an ISIN)."""
    if not isins:
        return {}
    since = today - timedelta(days=ANOMALY_WINDOW_DAYS)
    out: dict[str, list[tuple[date, float]]] = {}
    with db._conn() as conn:
        rows = conn.execute(
            """
            SELECT isin, trade_date,
                   sum(turnover) / NULLIF(sum(volume), 0) AS price
            FROM eod_trades
            WHERE isin = ANY(%s) AND trade_date >= %s
              AND price_basis = 'dirty_rupees'
              AND turnover IS NOT NULL AND volume IS NOT NULL
            GROUP BY isin, trade_date
            ORDER BY isin, trade_date
            """, (isins, since)).fetchall()
    for r in rows:
        if r["price"] is not None:
            out.setdefault(r["isin"], []).append(
                (r["trade_date"], float(r["price"])))
    return out


def _default_rows(isins: list[str]) -> dict[str, dict[str, list[dict]]]:
    """Default / technical-delay filings per ISIN (with the dates the
    detectors use for recency; bond_extras omits verification_date)."""
    out: dict[str, dict[str, list[dict]]] = {
        i: {"defaults": [], "technical_defaults": []} for i in isins}
    if not isins:
        return out
    with db._conn() as conn:
        for r in conn.execute(
                "SELECT isin, issue_nature, interest_due_date, "
                "redemption_due_date, default_details, verification_date "
                "FROM bond_defaults WHERE isin = ANY(%s)", (isins,)):
            out[r["isin"]]["defaults"].append(dict(r))
        for r in conn.execute(
                "SELECT isin, payment_due_date, amount_to_be_paid, "
                "amount_paid, failure_reason, update_date "
                "FROM bond_technical_defaults WHERE isin = ANY(%s)",
                (isins,)):
            out[r["isin"]]["technical_defaults"].append(dict(r))
    return out


def _users_with_lots() -> dict[int, dict[str, list[dict]]]:
    """{user_id: {isin: lots}} across all portfolios (units +
    purchase_date, for per-lot record-date checks)."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT user_id, isin, units, purchase_date "
            "FROM user_portfolio_lots").fetchall()
    out: dict[int, dict[str, list[dict]]] = {}
    for r in rows:
        out.setdefault(r["user_id"], {}).setdefault(r["isin"], []).append(
            {"units": float(r["units"]),
             "purchase_date": r["purchase_date"]})
    return out


def _insert(user_id: int, alerts: list[dict]) -> int:
    if not alerts:
        return 0
    inserted = 0
    with db._conn() as conn:
        for a in alerts:
            cur = conn.execute(
                """
                INSERT INTO user_notifications
                    (user_id, kind, severity, isin, title, body, dedupe_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, dedupe_key) DO NOTHING
                """,
                (user_id, a["kind"], a["severity"], a["isin"],
                 a["title"], a["body"], a["dedupe_key"]))
            inserted += cur.rowcount
    return inserted


def _label(core: dict, isin: str) -> str:
    return core.get("issuer_name") or isin


def generate(today: date | None = None) -> int:
    """Detect and insert notifications for every user with lots.
    Returns the number of newly inserted rows."""
    today = today or date.today()
    by_user = _users_with_lots()
    all_isins = sorted({i for lots in by_user.values() for i in lots})

    # Per-ISIN data is shared across users; fetch once.
    core: dict[str, dict] = {}
    history: dict[str, list[dict]] = {}
    flows: dict[str, list[schedule.Flow]] = {}
    raw_flows: dict[str, list[dict]] = {}
    for isin in all_isins:
        core[isin] = queries.analytics_row(isin, STALE_AFTER_DAYS) or {}
        history[isin] = queries.rating_history(isin).get(isin, [])
        raw_flows[isin] = queries.bond_cashflows(isin)
        flows[isin] = schedule.build(isin, raw_flows[isin],
                                     queries.bond_terms(isin),
                                     today=today).flows
    extras = _default_rows(all_isins)
    sessions = _price_sessions(all_isins, today)

    inserted = 0
    for user_id, lots_by_isin in by_user.items():
        alerts: list[dict] = []
        for isin, isin_lots in lots_by_isin.items():
            label = _label(core[isin], isin)
            alerts += rating_alerts(isin, label, history[isin], today)
            alerts += payment_alerts(isin, label, isin_lots, flows[isin],
                                     raw_flows[isin], today)
            alerts += default_alerts(isin, label,
                                     extras[isin]["defaults"],
                                     extras[isin]["technical_defaults"], today)
            alerts += anomaly_alerts(isin, label,
                                     sessions.get(isin, []), today)
        inserted += _insert(user_id, alerts)
    return inserted
