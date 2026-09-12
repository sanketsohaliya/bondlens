"""Portfolio notification generation.

Pure detection functions (unit-tested without a database) plus a thin
generate() that walks every user with portfolio lots and inserts rows
into user_notifications. The (user_id, dedupe_key) unique constraint
makes re-runs idempotent, so the cron can fire twice a day safely
(scripts/generate_notifications.py, after the NSDL and EOD parses).
"""
import itertools
import math
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
    return "unknown" if v is None or not math.isfinite(v) else f"\u20b9{v:,.2f}"


def _event_name(event: str) -> str:
    return "Redemption" if event == "redemption" else "Coupon"


# --- Pure detectors ---------------------------------------------------------
# Each returns a list of alert dicts:
# {kind, severity, isin, title, body, dedupe_key}

def rating_alerts(isin: str, label: str, history: list[dict],
                  today: date) -> list[dict]:
    """Recent rating movement on a held bond (ratings.latest_change)."""
    by_agency: dict[str, list[dict]] = {}
    for row in history:
        if row.get("agency") and row.get("rating_date") and row["rating_date"] <= today:
            by_agency.setdefault(row["agency"], []).append(row)
    alerts = {}
    for agency, rows in by_agency.items():
        dated = sorted(rows, key=lambda row: row["rating_date"])
        for previous, current in itertools.pairwise([{}] + dated):
            d = current["rating_date"]
            if not 0 <= (today - d).days <= RATING_WINDOW_DAYS:
                continue
            old, new = previous.get("rating"), current.get("rating")
            old_rank, new_rank = ratings.rank(old), ratings.rank(new)
            action = str(current.get("rating_action") or "").lower()
            kind, severity, change = "rating", "info", None
            if "withdraw" in action or "withdraw" in str(new).lower():
                if old != new or previous.get("rating_action") != current.get("rating_action"):
                    kind, severity, change = "rating_withdrawn", "warn", "rating withdrawn; this is not a downgrade to default"
            elif old_rank is not None and new_rank is not None and old_rank != new_rank:
                down = new_rank > old_rank
                severity = "warn" if down else "info"
                change = f"{'downgraded' if down else 'upgraded'} {old} → {new}"
            elif any(word in action for word in ("downgrad", "upgrad", "lowered", "raised")):
                if old != new or previous.get("rating_action") != current.get("rating_action"):
                    down = "downgrad" in action or "lowered" in action
                    severity = "warn" if down else "info"
                    change = f"reported {'downgrade' if down else 'upgrade'} to {new or 'unknown'}; previous grade {old or 'unknown'}"
            elif current.get("watch") and current.get("watch") != previous.get("watch"):
                kind, change = "rating_watch", f"watch: {current['watch']}"
                severity = "warn" if "negative" in str(current["watch"]).lower() else "info"
            elif previous.get("outlook") and current.get("outlook") and current["outlook"] != previous["outlook"]:
                kind, change = "rating_outlook", f"outlook {previous['outlook']} → {current['outlook']}"
                severity = "warn" if "negative" in str(current["outlook"]).lower() else "info"
            if change:
                key = f"rating:v2:{isin}:{agency}:{d}:{kind}:{new or ''}:{current.get('outlook') or ''}:{current.get('watch') or ''}"
                alerts[key] = {"kind": kind, "severity": severity, "isin": isin,
                               "title": f"{agency} {change} — {label}",
                               "body": f"Reported action dated {d:%d %b %Y}. {current.get('press_release') or ''}".strip(),
                               "dedupe_key": key}
    return list(alerts.values())


def _payment_report(flow: schedule.Flow, candidates: list[tuple[schedule.Flow, dict]]) -> dict | None:
    if flow.adjustments or flow.estimated or not candidates:
        return None
    key = (flow.record_date, flow.payment_date, flow.amount)
    if any((source.record_date, source.payment_date, source.amount) != key for source, _ in candidates):
        return None
    reports = {(row.get("actual_payment_date"), str(row.get("reason_delay_payment") or "").strip())
               for _, row in candidates}
    return candidates[0][1] if len(reports) == 1 else None


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
    raw_by_key: dict[tuple[str, date], list[tuple[schedule.Flow, dict]]] = {}
    for row in raw_rows:
        for source in schedule._normalize_rows([row]):
            raw_by_key.setdefault((source.event, source.due_date), []).append((source, row))

    signatures: dict[tuple[str, date], set[tuple]] = {}
    for flow in flows:
        signatures.setdefault((flow.event, flow.due_date), set()).add((flow.record_date, flow.payment_date, flow.amount, flow.optional))
    alerts = {}
    for f in flows:
        if len(signatures[(f.event, f.due_date)]) != 1 or (f.amount is not None and (not math.isfinite(f.amount) or f.amount < 0)):
            continue
        d = f.effective_date
        if not (1900 <= d.year < 2500 and 1900 <= f.due_date.year < 2500):
            continue
        if f.record_date is not None and (f.record_date > d or f.record_date.year < 1900):
            continue
        units = pf.receiving_units(lots, f)
        if not units or not math.isfinite(units) or f.amount == 0:
            continue
        amount = round(f.amount * units, 2) if f.amount is not None and math.isfinite(f.amount) and f.amount > 0 else None
        ev = _event_name(f.event).lower()
        qualifier = " (estimated)" if f.estimated else " (normalized)" if f.adjustments else ""
        raw = _payment_report(f, raw_by_key.get((f.event, f.due_date), []))
        paid_on = raw.get("actual_payment_date") if raw else None
        reason = str(raw.get("reason_delay_payment") or "").strip() if raw else ""
        paid = isinstance(paid_on, date) and 1900 <= paid_on.year < 2500 and paid_on <= today
        kind, severity, title = None, "info", ""
        body = (f"Scheduled/modelled amount {_inr(amount)} for {units:g} eligible units. "
                f"Contractual due {f.due_date:%d %b %Y}; expected payment {d:%d %b %Y}. "
                "Entitlement uses record-date checks; verify broker/issuer terms and bank credit.")
        if isinstance(paid_on, date) and paid and today - timedelta(days=CONFIRM_LOOKBACK_DAYS) <= paid_on:
            kind = "pay_confirmed"
            severity = "warn" if paid_on > d else "info"
            title = f"Issuer reports {ev} payment on {paid_on:%d %b %Y} — {label}"
            if paid_on > d:
                body += f" Reported {(paid_on - d).days} calendar days after the expected payment date."
            body += " Reported via NSDL; not confirmation of credit to your account."
        elif paid_on is not None:
            continue
        elif reason and today - timedelta(days=CONFIRM_LOOKBACK_DAYS) <= d <= today:
            kind, severity = "pay_overdue", "warn"
            title = f"Issuer reports delay: {ev} scheduled {d:%d %b %Y} — {label}"
            body += f" Issuer-filed reason: {reason}"
        elif today <= d <= today + timedelta(days=UPCOMING_DAYS):
            kind = "pay_today" if d == today else "pay_upcoming"
            title = f"Scheduled {ev} {_inr(amount)} on {d:%d %b %Y} — {label}{qualifier}"
        if kind:
            key = f"payment:v2:{isin}:{f.event}:{f.due_date}:{kind}"
            alerts[key] = {"kind": kind, "severity": severity, "isin": isin,
                           "title": title, "body": body, "dedupe_key": key}
    return list(alerts.values())


def default_alerts(isin: str, label: str, defaults: list[dict],
                   tech_defaults: list[dict], today: date) -> list[dict]:
    """New default / technical-delay filings against a held bond."""
    alerts: list[dict] = []
    for r in defaults:
        d = (r.get("verification_date") or r.get("interest_due_date")
             or r.get("redemption_due_date"))
        if d is None or not 0 <= (today - d).days <= DEFAULT_WINDOW_DAYS:
            continue
        detail = (r.get("default_details") or r.get("issue_nature") or "").strip()
        alerts.append({
            "kind": "default", "severity": "critical", "isin": isin,
            "title": f"Default filing dated {d:%d %b %Y} — {label}",
            "body": (f"{detail}. Verification: {r.get('verification_status') or 'unknown'}. "
                     f"Obligation dates: interest {r.get('interest_due_date') or 'unknown'}, principal {r.get('redemption_due_date') or 'unknown'}. "
                     f"Reported payment/update: {r.get('actual_payment_details') or 'unknown'}. Consult the source for current resolution status."),
            "dedupe_key": f"default:v2:{isin}:{r.get('interest_due_date')}:{r.get('redemption_due_date')}:{r.get('sr_no') or d}",
        })
    for r in tech_defaults:
        d = r.get("update_date") or r.get("payment_due_date")
        if d is None or not 0 <= (today - d).days <= DEFAULT_WINDOW_DAYS:
            continue
        parts = []
        if r.get("failure_reason"):
            parts.append(str(r["failure_reason"]).strip())
        if r.get("amount_to_be_paid") is not None:
            parts.append(f"due {_inr(float(r['amount_to_be_paid']))}, "
                         f"paid {_inr(float(r['amount_paid']) if r.get('amount_paid') is not None else None)}")
        alerts.append({
            "kind": "default", "severity": "warn", "isin": isin,
            "title": f"Technical payment delay reported \u2014 {label}",
            "body": f"Source update {d:%d %b %Y}; due date {r.get('payment_due_date') or 'unknown'}. " + "; ".join(parts) + ". Source-reported amounts, not your personal entitlement. This does not by itself establish a credit default or subsequent payment.",
            "dedupe_key": f"techdefault:v2:{isin}:{r.get('agency') or 'unknown'}:{r.get('payment_due_date')}",
        })
    return alerts


def anomaly_alerts(isin: str, label: str, sessions: list[tuple[date, float]],
                   today: date, flows: list[schedule.Flow] | None = None) -> list[dict]:
    """Latest traded price deviating > ANOMALY_PCT from the trailing
    median. ``sessions`` are (trade_date, price) ascending; the last one
    is compared against the median of the rest."""
    sessions = sorted((d, p) for d, p in sessions
                      if today - timedelta(days=ANOMALY_WINDOW_DAYS) <= d <= today
                      and p is not None and math.isfinite(p) and p > 0)
    if len({d for d, _ in sessions}) != len(sessions) or len(sessions) < ANOMALY_MIN_SESSIONS + 1:
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
    payment_context = any(not f.optional and any(d is not None and sessions[0][0] <= d <= last_d
                                                for d in (f.record_date, f.effective_date)) for f in flows or [])
    return [{
        "kind": "anomaly",
        "severity": "warn" if drop else "info",
        "isin": isin,
        "title": f"{label}: daily average price {abs(dev):.1f}% "
                 f"{'below' if drop else 'above'} the median of {len(prior)} prior sessions",
        "body": (f"Turnover-weighted observed price {_inr(last_p)} on {last_d:%d %b %Y} vs median {_inr(med)} "
                 f"within {ANOMALY_WINDOW_DAYS} calendar days; not a live offer or a credit diagnosis. Check trading size and liquidity. "
                 + ("A coupon/principal payment or record date falls in this window; mechanical price changes may affect this comparison. Verify the ex-date." if payment_context else "")),
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
            WHERE isin = ANY(%s) AND trade_date BETWEEN %s AND %s
              AND segment = 'retail' AND price_basis = 'dirty_rupees'
              AND turnover > 0 AND volume > 0
            GROUP BY isin, trade_date
            ORDER BY isin, trade_date
            """, (isins, since, today)).fetchall()
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
                "SELECT isin, sr_no, issue_nature, interest_due_date, "
                "redemption_due_date, default_details, verification_date, verification_status, actual_payment_details "
                "FROM bond_defaults WHERE isin = ANY(%s)", (isins,)):
            out[r["isin"]]["defaults"].append(dict(r))
        for r in conn.execute(
                "SELECT isin, agency, payment_due_date, amount_to_be_paid, "
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
                ON CONFLICT (user_id, dedupe_key) DO UPDATE SET
                    title = EXCLUDED.title, body = EXCLUDED.body, severity = EXCLUDED.severity,
                    read_at = NULL, created_at = now()
                WHERE (user_notifications.title, user_notifications.body, user_notifications.severity)
                    IS DISTINCT FROM (EXCLUDED.title, EXCLUDED.body, EXCLUDED.severity)
                """,
                (user_id, a["kind"], a["severity"], a["isin"],
                 a["title"], a["body"], a["dedupe_key"]))
            inserted += cur.rowcount
    return inserted


def _label(core: dict, isin: str) -> str:
    symbol = core.get("nse_symbol") or core.get("bse_scrip_id") or core.get("issuer_name")
    return f"{symbol} ({isin})" if symbol else isin


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
                                     sessions.get(isin, []), today, flows[isin])
        inserted += _insert(user_id, alerts)
    return inserted
