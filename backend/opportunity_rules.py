"""Persistence for opportunity alerts: rules, exit targets, the per-user
ntfy topic, the fired-alert ledger and the watcher heartbeat.

Candidate resolution lives here too: a rule's static filters are run
through the explorer query exactly as the Explore page runs them, so the
watcher watches precisely what that screen would show.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from . import db, opportunities, queries
from .analytics import ratings
from .analytics.compute import STALE_AFTER_DAYS

TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
IST = ZoneInfo("Asia/Kolkata")


class RuleError(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


# --- Change marker ----------------------------------------------------------

def touch(user_id: int) -> None:
    """Record that this user's rules/targets/topic changed (watcher polls)."""
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO alert_changes (user_id, changed_at) VALUES (%s, now()) "
            "ON CONFLICT (user_id) DO UPDATE SET changed_at = now()", (user_id,))


def changed_since(since) -> dict[int, Any]:
    """user_id -> changed_at for users whose alert setup changed after `since`."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT user_id, changed_at FROM alert_changes WHERE changed_at > %s",
            (since,)).fetchall()
    return {r["user_id"]: r["changed_at"] for r in rows}


# --- Rules ------------------------------------------------------------------

def _row(r: Any) -> dict:
    d = dict(r)
    for k in ("min_ask_ytm", "max_ticket", "max_price", "min_qty", "buy_budget", "buy_daily_cap",
              "min_bid_price", "max_bid_ytm", "below_threshold", "price", "value",
              "auto_buy_daily_cap"):
        if k in d and d[k] is not None:
            d[k] = float(d[k])
    if "filters" in d and "min_ask_ytm" in d:          # an alert_rules row
        d["label"] = opportunities.rule_label(d)
        until = d.get("auto_buy_until")
        d["auto_buy"] = bool(until and until > datetime.now(UTC))
    elif "min_bid_price" in d and "isin" in d and "source" not in d:   # an exit_targets row
        d["label"] = opportunities.target_label(d)
    return d


def validate_rule(payload: dict) -> dict:
    """Normalise and check a rule body; raises RuleError(400)."""
    filters = payload.get("filters") or {}
    if not isinstance(filters, dict):
        raise RuleError(400, "filters must be an object")
    rejected = opportunities.rejected_filters(filters)
    if rejected:
        raise RuleError(400, "trade-history filters do not apply to live asks: "
                        + ", ".join(rejected))
    if filters.get("min_rating") and ratings.RATING_RANK.get(
            str(filters["min_rating"]).strip().upper()) is None:
        raise RuleError(400, f"unknown rating grade {filters['min_rating']!r}")
    out = {"filters": opportunities.static_filters(filters),
           "enabled": bool(payload.get("enabled", True))}
    for k in ("min_ask_ytm", "max_ticket", "max_price", "min_qty", "buy_budget", "buy_daily_cap"):
        v = payload.get(k)
        if v in (None, ""):
            out[k] = None
            continue
        try:
            f = float(v)
        except (TypeError, ValueError) as exc:
            raise RuleError(400, f"{k} must be a number") from exc
        if not (f > 0) or f != f:
            raise RuleError(400, f"{k} must be positive")
        out[k] = f
    if out["min_ask_ytm"] is None and out["max_price"] is None:
        raise RuleError(400, "set a minimum YTM @ Ask or a maximum price")
    if (out["buy_budget"] is None) != (out["buy_daily_cap"] is None):
        raise RuleError(400, "auto-buy needs both a budget per hit and a daily cap")
    if out["buy_budget"] is not None and out["buy_daily_cap"] is not None \
            and out["buy_budget"] > out["buy_daily_cap"]:
        raise RuleError(400, "budget per hit cannot exceed the daily cap")
    return out


def list_rules(user_id: int) -> list[dict]:
    with db._conn() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM alert_rules WHERE user_id = %s ORDER BY id", (user_id,)).fetchall()]


def create_rule(user_id: int, payload: dict) -> dict:
    v = validate_rule(payload)
    with db._conn() as conn:
        r = conn.execute(
            "INSERT INTO alert_rules (user_id, filters, min_ask_ytm, max_ticket, "
            " max_price, min_qty, enabled, buy_budget, buy_daily_cap) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (user_id, Jsonb(v["filters"]), v["min_ask_ytm"], v["max_ticket"],
             v["max_price"], v["min_qty"], v["enabled"], v["buy_budget"],
             v["buy_daily_cap"])).fetchone()
    assert r is not None
    touch(user_id)
    return _row(r)


def update_rule(user_id: int, rule_id: int, payload: dict) -> dict:
    with db._conn() as conn:
        current = conn.execute(
            "SELECT * FROM alert_rules WHERE id = %s AND user_id = %s",
            (rule_id, user_id)).fetchone()
        if current is None:
            raise RuleError(404, "rule not found")
        merged = {**_row(current), **payload}
        v = validate_rule(merged)
        r = conn.execute(
            "UPDATE alert_rules SET filters = %s, min_ask_ytm = %s, max_ticket = %s, "
            " max_price = %s, min_qty = %s, enabled = %s, buy_budget = %s, buy_daily_cap = %s, "
            " auto_buy_until = CASE WHEN %s IS NULL THEN NULL ELSE auto_buy_until END, "
            " updated_at = now() "
            "WHERE id = %s AND user_id = %s RETURNING *",
            (Jsonb(v["filters"]), v["min_ask_ytm"], v["max_ticket"], v["max_price"],
             v["min_qty"], v["enabled"], v["buy_budget"], v["buy_daily_cap"], v["buy_budget"],
             rule_id, user_id)).fetchone()
    assert r is not None
    touch(user_id)
    return _row(r)


def delete_rule(user_id: int, rule_id: int) -> None:
    with db._conn() as conn:
        cur = conn.execute("DELETE FROM alert_rules WHERE id = %s AND user_id = %s",
                           (rule_id, user_id))
        if cur.rowcount == 0:
            raise RuleError(404, "rule not found")
    touch(user_id)


def all_enabled_rules() -> list[dict]:
    with db._conn() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM alert_rules WHERE enabled ORDER BY user_id, id").fetchall()]


# --- Exit targets -----------------------------------------------------------

def validate_target(payload: dict) -> dict:
    out: dict[str, Any] = {"enabled": bool(payload.get("enabled", True))}
    for k in ("min_bid_price", "max_bid_ytm"):
        v = payload.get(k)
        if v in (None, ""):
            out[k] = None
            continue
        try:
            f = float(v)
        except (TypeError, ValueError) as exc:
            raise RuleError(400, f"{k} must be a number") from exc
        if not (f > 0) or f != f:
            raise RuleError(400, f"{k} must be positive")
        out[k] = f
    if out["min_bid_price"] is None and out["max_bid_ytm"] is None:
        raise RuleError(400, "set a minimum bid price or a maximum bid YTM")
    return out


def list_targets(user_id: int) -> list[dict]:
    with db._conn() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM exit_targets WHERE user_id = %s ORDER BY isin", (user_id,)).fetchall()]


def put_target(user_id: int, isin: str, payload: dict) -> dict:
    v = validate_target(payload)
    with db._conn() as conn:
        r = conn.execute(
            "INSERT INTO exit_targets (user_id, isin, min_bid_price, max_bid_ytm, enabled) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (user_id, isin) DO UPDATE SET "
            " min_bid_price = EXCLUDED.min_bid_price, max_bid_ytm = EXCLUDED.max_bid_ytm, "
            " enabled = EXCLUDED.enabled, updated_at = now() RETURNING *",
            (user_id, isin, v["min_bid_price"], v["max_bid_ytm"], v["enabled"])).fetchone()
    assert r is not None
    touch(user_id)
    return _row(r)


def delete_target(user_id: int, isin: str) -> None:
    with db._conn() as conn:
        cur = conn.execute("DELETE FROM exit_targets WHERE user_id = %s AND isin = %s",
                           (user_id, isin))
        if cur.rowcount == 0:
            raise RuleError(404, "target not found")
    touch(user_id)


def all_enabled_targets() -> list[dict]:
    with db._conn() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM exit_targets WHERE enabled ORDER BY user_id, isin").fetchall()]


# --- Settings ---------------------------------------------------------------

def get_topic(user_id: int) -> str | None:
    with db._conn() as conn:
        r = conn.execute("SELECT ntfy_topic FROM users WHERE id = %s", (user_id,)).fetchone()
    return r["ntfy_topic"] if r else None


def set_topic(user_id: int, topic: str | None) -> str | None:
    topic = (topic or "").strip() or None
    if topic is not None and not TOPIC_RE.match(topic):
        raise RuleError(400, "topic must be 6-64 letters, digits, '-' or '_'")
    with db._conn() as conn:
        conn.execute("UPDATE users SET ntfy_topic = %s WHERE id = %s", (topic, user_id))
    touch(user_id)
    return topic


def market_close_today() -> datetime:
    """15:45 IST today - when an armed rule disarms itself."""
    ist = datetime.now(IST)
    return ist.replace(hour=15, minute=45, second=0, microsecond=0)


def arm_auto_buy(user_id: int, rule_id: int, on: bool) -> dict:
    with db._conn() as conn:
        current = conn.execute("SELECT * FROM alert_rules WHERE id = %s AND user_id = %s",
                               (rule_id, user_id)).fetchone()
        if current is None:
            raise RuleError(404, "rule not found")
        if on and (current["buy_budget"] is None or current["buy_daily_cap"] is None):
            raise RuleError(400, "set a budget per hit and a daily cap before arming")
        if on and datetime.now(IST) >= market_close_today():
            raise RuleError(400, "market is closed; arm it tomorrow")
        r = conn.execute(
            "UPDATE alert_rules SET auto_buy_until = %s, updated_at = now() "
            "WHERE id = %s AND user_id = %s RETURNING *",
            (market_close_today() if on else None, rule_id, user_id)).fetchone()
    assert r is not None
    touch(user_id)
    return _row(r)


def disarm_all(user_id: int) -> int:
    with db._conn() as conn:
        cur = conn.execute("UPDATE alert_rules SET auto_buy_until = NULL "
                           "WHERE user_id = %s AND auto_buy_until IS NOT NULL", (user_id,))
    touch(user_id)
    return cur.rowcount


def get_auto_buy_cap(user_id: int) -> float | None:
    with db._conn() as conn:
        r = conn.execute("SELECT auto_buy_daily_cap FROM users WHERE id = %s", (user_id,)).fetchone()
    return float(r["auto_buy_daily_cap"]) if r and r["auto_buy_daily_cap"] is not None else None


def set_auto_buy_cap(user_id: int, cap: float | None) -> float | None:
    if cap is not None and not cap > 0:
        raise RuleError(400, "daily cap must be positive")
    with db._conn() as conn:
        conn.execute("UPDATE users SET auto_buy_daily_cap = %s WHERE id = %s", (cap, user_id))
    touch(user_id)
    return cap


def auto_spend_today(user_id: int, today: date) -> dict[str, Any]:
    """{'by_rule': {rule_id: ₹ placed}, 'total': ₹, 'ordered': {(rule_id, isin)}} for today."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT rule_id, isin, value, status FROM auto_orders "
            "WHERE user_id = %s AND trade_day = %s", (user_id, today)).fetchall()
    by_rule: dict[int, float] = {}
    total = 0.0
    ordered: set[tuple[int, str]] = set()
    for r in rows:
        ordered.add((r["rule_id"], r["isin"]))
        if r["status"] == "placed":
            by_rule[r["rule_id"]] = by_rule.get(r["rule_id"], 0.0) + float(r["value"])
            total += float(r["value"])
    return {"by_rule": by_rule, "total": total, "ordered": ordered}


def record_auto_order(user_id: int, *, rule_id: int, rule_label: str, alert_id: int | None,
                      isin: str, exchange: str, tradingsymbol: str | None, qty: int, price: float,
                      today: date, status: str, reason: str | None = None,
                      kite_order_id: str | None = None) -> int | None:
    """Insert the attempt; None when this bond/rule/day already has one
    (the unique key is the race guard for two ticks on the same bond)."""
    with db._conn() as conn:
        r = conn.execute(
            "INSERT INTO auto_orders (user_id, rule_id, rule_label, alert_id, isin, exchange, "
            " tradingsymbol, qty, price, value, trade_day, status, reason, kite_order_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (user_id, rule_id, isin, trade_day) DO NOTHING RETURNING id",
            (user_id, rule_id, rule_label, alert_id, isin, exchange, tradingsymbol, qty, price,
             qty * price, today, status, reason, kite_order_id)).fetchone()
    return r["id"] if r else None


def finish_auto_order(order_id: int, *, status: str, reason: str | None,
                      kite_order_id: str | None) -> None:
    with db._conn() as conn:
        conn.execute("UPDATE auto_orders SET status = %s, reason = %s, kite_order_id = %s "
                     "WHERE id = %s", (status, reason, kite_order_id, order_id))


def recent_auto_orders(user_id: int, today: date | None = None, limit: int = 100) -> list[dict]:
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT o.*, b.issuer_name FROM auto_orders o "
            "LEFT JOIN bond_analytics b ON b.isin = o.isin "
            "WHERE o.user_id = %s AND (%s::date IS NULL OR o.trade_day = %s) "
            "ORDER BY o.placed_at DESC LIMIT %s", (user_id, today, today, limit)).fetchall()
    return [_row(r) for r in rows]


def topics_by_user() -> dict[int, str]:
    with db._conn() as conn:
        return {r["id"]: r["ntfy_topic"] for r in conn.execute(
            "SELECT id, ntfy_topic FROM users WHERE ntfy_topic IS NOT NULL").fetchall()}


# --- Candidates -------------------------------------------------------------

def explorer_params(filters: dict) -> dict:
    """Rule filters -> queries.explorer params (same mapping as /api/bonds)."""
    f = dict(opportunities.static_filters(filters))
    if "min_rating" in f:
        f["min_rating_rank"] = ratings.RATING_RANK.get(str(f.pop("min_rating")).strip().upper())
    f["limit"], f["offset"] = 100_000, 0
    f["exclude_maturity_mismatch"] = True
    return f


def candidates_for(rules: list[dict]) -> dict[int, list[str]]:
    """rule id -> ISINs its static filters select right now (the Explore
    screen's own query, so a rule watches exactly what the screen shows)."""
    out: dict[int, list[str]] = {}
    for rule in rules:
        params = explorer_params(rule.get("filters") or {})
        if "min_rating_rank" in params and params["min_rating_rank"] is None:
            out[rule["id"]] = []
            continue
        rows, _, _ = queries.explorer(params, STALE_AFTER_DAYS)
        out[rule["id"]] = [r["isin"] for r in rows]
    return out


# --- Ledger -----------------------------------------------------------------

def todays_hits(user_id: int, today: date) -> dict[tuple[str, str], dict]:
    """(source, isin) -> latest fired alert today (for dedupe on start)."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ON (source, isin) source, isin, side, price, qty, ytm, exchange "
            "FROM opportunity_alerts WHERE user_id = %s AND trade_day = %s "
            "ORDER BY source, isin, fired_at DESC", (user_id, today)).fetchall()
    return {(r["source"], r["isin"]): _row(r) for r in rows}


def record_hit(user_id: int, hit: opportunities.Hit, today: date, *, tick_at=None,
               pushed_at=None, push_ms: int | None = None,
               below_threshold: float | None = None) -> int:
    with db._conn() as conn:
        r = conn.execute(
            "INSERT INTO opportunity_alerts (user_id, source, isin, side, price, qty, ytm, "
            " exchange, trade_day, tick_at, pushed_at, push_ms, below_threshold, rule_label) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (user_id, hit.source, hit.isin, hit.side, hit.price, hit.qty, hit.ytm,
             hit.exchange, today, tick_at, pushed_at, push_ms, below_threshold,
             hit.rule_label)).fetchone()
        if hit.source.startswith("rule:"):
            conn.execute("UPDATE alert_rules SET last_hit_at = now() WHERE id = %s",
                         (int(hit.source.split(":", 1)[1]),))
    assert r is not None
    return r["id"]


def hits_today(user_id: int, today: date) -> dict[str, int]:
    """source -> number of alerts fired today (the panel's 'N hits today')."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT source, count(*) AS n FROM opportunity_alerts "
            "WHERE user_id = %s AND trade_day = %s GROUP BY source", (user_id, today)).fetchall()
    return {r["source"]: int(r["n"]) for r in rows}


def recent_hits(user_id: int, *, today: date | None = None, limit: int = 200) -> list[dict]:
    """Fired alerts, newest first - today's only when `today` is given.
    Hits that predate stored labels get theirs derived from the rule (or
    target) they came from, so every hit reads the same."""
    args: list[Any] = [user_id, today, today, limit]
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT a.*, b.issuer_name, b.nse_symbol, b.bse_scrip_id, "
            " r.filters AS r_filters, r.min_ask_ytm AS r_min_ask_ytm, r.max_ticket AS r_max_ticket, "
            " r.max_price AS r_max_price, r.min_qty AS r_min_qty, "
            " t.min_bid_price AS t_min_bid_price, t.max_bid_ytm AS t_max_bid_ytm "
            "FROM opportunity_alerts a "
            "LEFT JOIN bond_analytics b ON b.isin = a.isin "
            "LEFT JOIN alert_rules r ON a.source = 'rule:' || r.id "
            "LEFT JOIN exit_targets t ON a.source = 'target' AND t.user_id = a.user_id AND t.isin = a.isin "
            "WHERE a.user_id = %s AND a.source <> 'nudge' AND (%s::date IS NULL OR a.trade_day = %s) "
            "ORDER BY a.fired_at DESC LIMIT %s", args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if not d.get("rule_label"):
            if d.get("r_filters") is not None or d.get("r_min_ask_ytm") is not None:
                d["rule_label"] = opportunities.rule_label({
                    "filters": d["r_filters"], "min_ask_ytm": d["r_min_ask_ytm"],
                    "max_ticket": d["r_max_ticket"], "max_price": d["r_max_price"],
                    "min_qty": d["r_min_qty"]})
            elif d["source"] == "target":
                d["rule_label"] = opportunities.target_label({
                    "min_bid_price": d["t_min_bid_price"], "max_bid_ytm": d["t_max_bid_ytm"]})
            else:
                d["rule_label"] = f"Deleted rule #{d['source'].split(':')[-1]}"
        for k in list(d):
            if k.startswith(("r_", "t_")):
                del d[k]
        out.append(_row(d))
    return out


# --- Heartbeat --------------------------------------------------------------

def heartbeat(users: int, tokens: int, *, p50: int | None = None, p99: int | None = None,
              thresholds_ready: bool = False, note: str | None = None) -> None:
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO watcher_heartbeat (id, beat_at, users, tokens, push_p50_ms, "
            " push_p99_ms, thresholds_ready, note) VALUES (1, now(), %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET beat_at = now(), users = EXCLUDED.users, "
            " tokens = EXCLUDED.tokens, push_p50_ms = EXCLUDED.push_p50_ms, "
            " push_p99_ms = EXCLUDED.push_p99_ms, thresholds_ready = EXCLUDED.thresholds_ready, "
            " note = EXCLUDED.note",
            (users, tokens, p50, p99, thresholds_ready, note))


def read_heartbeat() -> dict | None:
    with db._conn() as conn:
        r = conn.execute("SELECT * FROM watcher_heartbeat WHERE id = 1").fetchone()
    return dict(r) if r else None
