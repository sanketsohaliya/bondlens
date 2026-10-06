"""Persistence for opportunity alerts: rules, exit targets, the per-user
ntfy topic, the fired-alert ledger and the watcher heartbeat.

Candidate resolution lives here too: a rule's static filters are run
through the explorer query exactly as the Explore page runs them, so the
watcher watches precisely what that screen would show.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from psycopg.types.json import Jsonb

from . import db, opportunities, queries
from .analytics import ratings
from .analytics.compute import STALE_AFTER_DAYS

TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


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
    for k in ("min_ask_ytm", "max_ticket", "max_price", "min_qty",
              "min_bid_price", "max_bid_ytm", "below_threshold"):
        if k in d and d[k] is not None:
            d[k] = float(d[k])
    return d


def validate_rule(payload: dict) -> dict:
    """Normalise and check a rule body; raises RuleError(400)."""
    name = str(payload.get("name") or "").strip()
    if not 1 <= len(name) <= 60:
        raise RuleError(400, "name must be 1-60 characters")
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
    out = {"name": name, "filters": opportunities.static_filters(filters),
           "enabled": bool(payload.get("enabled", True))}
    for k in ("min_ask_ytm", "max_ticket", "max_price", "min_qty"):
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
    return out


def list_rules(user_id: int) -> list[dict]:
    with db._conn() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM alert_rules WHERE user_id = %s ORDER BY id", (user_id,)).fetchall()]


def create_rule(user_id: int, payload: dict) -> dict:
    v = validate_rule(payload)
    with db._conn() as conn:
        r = conn.execute(
            "INSERT INTO alert_rules (user_id, name, filters, min_ask_ytm, max_ticket, "
            " max_price, min_qty, enabled) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (user_id, v["name"], Jsonb(v["filters"]), v["min_ask_ytm"], v["max_ticket"],
             v["max_price"], v["min_qty"], v["enabled"])).fetchone()
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
            "UPDATE alert_rules SET name = %s, filters = %s, min_ask_ytm = %s, max_ticket = %s, "
            " max_price = %s, min_qty = %s, enabled = %s, updated_at = now() "
            "WHERE id = %s AND user_id = %s RETURNING *",
            (v["name"], Jsonb(v["filters"]), v["min_ask_ytm"], v["max_ticket"], v["max_price"],
             v["min_qty"], v["enabled"], rule_id, user_id)).fetchone()
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
            " exchange, trade_day, tick_at, pushed_at, push_ms, below_threshold) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (user_id, hit.source, hit.isin, hit.side, hit.price, hit.qty, hit.ytm,
             hit.exchange, today, tick_at, pushed_at, push_ms, below_threshold)).fetchone()
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


def recent_hits(user_id: int, limit: int = 200) -> list[dict]:
    with db._conn() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT a.*, b.issuer_name, b.nse_symbol, b.bse_scrip_id "
            "FROM opportunity_alerts a LEFT JOIN bond_analytics b ON b.isin = a.isin "
            "WHERE a.user_id = %s AND a.source <> 'nudge' "
            "ORDER BY a.fired_at DESC LIMIT %s", (user_id, limit)).fetchall()]


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
