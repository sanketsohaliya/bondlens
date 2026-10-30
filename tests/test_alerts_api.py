"""Alerts API: routing, validation and per-user isolation with the
persistence layer faked in memory (CI has no Postgres). Rule validation
itself (opportunity_rules.validate_rule) is real."""
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from backend import ntfy, queries
from backend import opportunity_rules as rules
from backend.app import app
from backend.auth import require_user

NOW = datetime(2026, 10, 5, 4, tzinfo=UTC)


class FakeStore:
    def __init__(self):
        self.rules: dict[int, dict] = {}
        self.targets: dict[tuple[int, str], dict] = {}
        self.topics: dict[int, str | None] = {}
        self.next_id = 1
        self.pushed: list[tuple] = []
        self.cap = None
        self.blacklisted: dict[tuple[int, str], str | None] = {}

    def list_rules(self, user_id):
        return [r for r in self.rules.values() if r["user_id"] == user_id]

    def create_rule(self, user_id, payload):
        v = rules.validate_rule(payload)
        row = {**v, "id": self.next_id, "user_id": user_id, "created_at": NOW,
               "updated_at": NOW, "last_hit_at": None}
        row["label"] = rules.opportunities.rule_label(row)
        self.rules[self.next_id] = row
        self.next_id += 1
        return row

    def update_rule(self, user_id, rule_id, payload):
        row = self.rules.get(rule_id)
        if row is None or row["user_id"] != user_id:
            raise rules.RuleError(404, "rule not found")
        v = rules.validate_rule({**row, **payload})
        row.update(v)
        row["label"] = rules.opportunities.rule_label(row)
        return row

    def delete_rule(self, user_id, rule_id):
        row = self.rules.get(rule_id)
        if row is None or row["user_id"] != user_id:
            raise rules.RuleError(404, "rule not found")
        del self.rules[rule_id]

    def list_targets(self, user_id):
        return [t for (u, _), t in self.targets.items() if u == user_id]

    def put_target(self, user_id, isin, payload):
        v = rules.validate_target(payload)
        row = {**v, "isin": isin, "user_id": user_id, "updated_at": NOW,
               "label": rules.opportunities.target_label(v)}
        self.targets[(user_id, isin)] = row
        return row

    def delete_target(self, user_id, isin):
        if (user_id, isin) not in self.targets:
            raise rules.RuleError(404, "target not found")
        del self.targets[(user_id, isin)]

    def get_topic(self, user_id):
        return self.topics.get(user_id)

    def set_topic(self, user_id, topic):
        topic = (topic or "").strip() or None
        if topic is not None and not rules.TOPIC_RE.match(topic):
            raise rules.RuleError(400, "bad topic")
        self.topics[user_id] = topic
        return topic

    def recent_hits(self, user_id, *, today=None, limit=200):
        self.recent_scope = "today" if today else "all"
        return []

    def hits_today(self, user_id, today):
        return {"rule:1": 3}

    def recent_auto_orders(self, user_id, today=None, limit=100):
        return []

    def auto_spend_today(self, user_id, today):
        return {"by_rule": {1: 9970.0}, "total": 9970.0, "ordered": {(1, "INE000A01001")}}

    def get_auto_buy_cap(self, user_id):
        return self.cap

    def set_auto_buy_cap(self, user_id, cap):
        if cap is not None and not cap > 0:
            raise rules.RuleError(400, "daily cap must be positive")
        self.cap = cap
        return cap

    def arm_auto_buy(self, user_id, rule_id, on):
        row = self.rules.get(rule_id)
        if row is None or row["user_id"] != user_id:
            raise rules.RuleError(404, "rule not found")
        if on and (row.get("buy_budget") is None or row.get("buy_daily_cap") is None):
            raise rules.RuleError(400, "set a budget per hit and a daily cap before arming")
        row["auto_buy"] = on
        row["auto_buy_until"] = NOW if on else None
        return row

    def disarm_all(self, user_id):
        for r in self.rules.values():
            r["auto_buy"] = False
        return 0

    def read_heartbeat(self):
        return None

    # --- never-auto-buy list ---
    def list_blacklist(self, user_id):
        return [{"isin": i, "note": n, "created_at": NOW, "issuer_name": None}
                for (u, i), n in self.blacklisted.items() if u == user_id]

    def put_blacklist(self, user_id, isin, note):
        isin = isin.strip().upper()
        if not rules.ISIN_RE.match(isin):
            raise rules.RuleError(400, "not an ISIN")
        self.blacklisted[(user_id, isin)] = (note or "").strip()[:60] or None
        return {"isin": isin, "note": self.blacklisted[(user_id, isin)], "created_at": NOW, "issuer_name": None}

    def delete_blacklist(self, user_id, isin):
        self.blacklisted.pop((user_id, isin.strip().upper()), None)


@pytest.fixture
def client(monkeypatch):
    store = FakeStore()
    for name in ("list_rules", "create_rule", "update_rule", "delete_rule", "list_targets",
                 "put_target", "delete_target", "get_topic", "set_topic", "recent_hits",
                 "hits_today", "read_heartbeat", "recent_auto_orders", "auto_spend_today",
                 "get_auto_buy_cap", "set_auto_buy_cap", "arm_auto_buy", "disarm_all",
                 "list_blacklist", "put_blacklist", "delete_blacklist"):
        monkeypatch.setattr(rules, name, getattr(store, name))
    monkeypatch.setattr(queries, "bond_row", lambda isin: {"isin": isin} if isin.startswith("INE") else None)
    monkeypatch.setattr(ntfy, "push", lambda topic, title, body, **kw: store.pushed.append((topic, title)) or 12)
    monkeypatch.setattr("backend.app.kite_connect.configured", lambda: True)
    monkeypatch.setattr("backend.app.kite_connect.token_for", lambda uid: "tok")
    current = {"id": 1}
    app.dependency_overrides[require_user] = lambda: current["id"]
    try:
        yield TestClient(app), store, current
    finally:
        app.dependency_overrides.pop(require_user, None)


RULE = {"filters": {"min_rating": "BBB-", "min_years": 0.5}, "min_ask_ytm": 12, "min_ticket": 50000}


def test_rule_lifecycle_and_overview(client):
    c, store, _ = client
    r = c.post("/api/alerts/rules", json=RULE)
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["id"] == 1 and rule["filters"] == {"min_rating": "BBB-", "min_years": 0.5}
    assert rule["label"] == "Ask YTM ≥ 12%, Ticket ≥ ₹50,000, Rating ≥ BBB-, Maturity ≥ 6mo"
    assert rule["min_ticket"] == 50000 and rule["enabled"] is True
    r = c.patch("/api/alerts/rules/1", json={"enabled": False, "min_ticket": 75000})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["min_ticket"] == 75000
    over = c.get("/api/alerts").json()
    assert [x["id"] for x in over["rules"]] == [1]
    assert over["targets"] == [] and over["recent"] == [] and over["watcher"] is None
    assert over["hits_today"] == {"rule:1": 3} and store.recent_scope == "today"
    assert over["spent_today"] == {"rule:1": 9970.0, "total": 9970.0} and over["auto_orders"] == []
    assert over["settings"] == {"ntfy_topic": None, "auto_buy_daily_cap": None}
    c.get("/api/alerts?hits=all")
    assert store.recent_scope == "all"
    assert c.get("/api/alerts?hits=sometimes").status_code == 422
    assert c.delete("/api/alerts/rules/1").json() == {"ok": True}
    assert c.delete("/api/alerts/rules/1").status_code == 404


def test_rules_refuse_trade_history_filters_and_empty_conditions(client):
    c, _, _ = client
    r = c.post("/api/alerts/rules", json={**RULE, "filters": {"min_rating": "BBB-", "traded_within": 5, "min_ytm": 5}})
    assert r.status_code == 400 and "min_ytm, traded_within" in r.json()["detail"]
    r = c.post("/api/alerts/rules", json={"filters": {}, "min_ticket": 1000})
    assert r.status_code == 400 and "minimum YTM @ Ask" in r.json()["detail"]
    r = c.post("/api/alerts/rules", json={**RULE, "filters": {"min_rating": "ZZZ"}})
    assert r.status_code == 400 and "unknown rating" in r.json()["detail"]
    assert c.post("/api/alerts/rules", json={**RULE, "min_ask_ytm": -1}).status_code == 422


def test_rules_and_targets_are_isolated_per_user(client):
    c, store, current = client
    c.post("/api/alerts/rules", json=RULE)
    c.put("/api/alerts/targets/INE000A01001", json={"min_bid_price": 1010})
    current["id"] = 2
    assert c.get("/api/alerts").json()["rules"] == []
    assert c.get("/api/alerts").json()["targets"] == []
    assert c.patch("/api/alerts/rules/1", json={"enabled": False}).status_code == 404
    assert c.delete("/api/alerts/targets/INE000A01001").status_code == 404
    current["id"] = 1
    assert len(c.get("/api/alerts").json()["targets"]) == 1


def test_exit_target_validation(client):
    c, _, _ = client
    r = c.put("/api/alerts/targets/ine000a01001", json={"min_bid_price": 1010, "max_bid_ytm": 9})
    assert r.status_code == 200 and r.json()["isin"] == "INE000A01001"
    assert c.put("/api/alerts/targets/XX0000000001", json={"min_bid_price": 1}).status_code == 404
    r = c.put("/api/alerts/targets/INE000A01001", json={})
    assert r.status_code == 400 and "minimum bid price" in r.json()["detail"]
    assert c.delete("/api/alerts/targets/INE000A01001").json() == {"ok": True}


def test_topic_settings_and_test_push(client):
    c, store, _ = client
    assert c.post("/api/alerts/test").status_code == 400  # no topic yet
    assert c.put("/api/alerts/settings", json={"ntfy_topic": "short"}).status_code == 400
    r = c.put("/api/alerts/settings", json={"ntfy_topic": "bondlens-opps-abc123"})
    assert r.json() == {"ntfy_topic": "bondlens-opps-abc123", "auto_buy_daily_cap": None}
    assert c.post("/api/alerts/test").json() == {"ok": True}
    assert store.pushed == [("bondlens-opps-abc123", "BondLens alerts are wired")]
    assert c.put("/api/alerts/settings", json={"ntfy_topic": None}).json() == {"ntfy_topic": None, "auto_buy_daily_cap": None}
    r = c.put("/api/alerts/settings", json={"auto_buy_daily_cap": 100000})
    assert r.json() == {"ntfy_topic": None, "auto_buy_daily_cap": 100000.0}
    assert c.put("/api/alerts/settings", json={"auto_buy_daily_cap": -1}).status_code == 400


def test_validate_rule_normalises_numbers_and_filters():
    v = rules.validate_rule({"filters": {"min_rating": "aa-", "sector": ""},
                             "min_ask_ytm": "12.5", "min_ticket": None})
    assert v["min_ask_ytm"] == 12.5 and v["min_ticket"] is None
    assert v["filters"] == {"min_rating": "aa-"}
    with pytest.raises(rules.RuleError):
        rules.validate_rule({"min_ask_ytm": "lots"})


def test_explorer_params_map_like_the_bonds_endpoint():
    p = rules.explorer_params({"min_rating": "BBB-", "min_years": 1, "traded_within": 5})
    assert p["min_rating_rank"] is not None and "min_rating" not in p
    assert p["min_years"] == 1 and "traded_within" not in p and p["limit"] == 100_000
    assert p["exclude_maturity_mismatch"] is True   # never alert on a contradicted schedule


def test_ntfy_header_encoding_round_trips_non_latin_titles():
    assert ntfy._header_value("plain") == "plain"
    enc = ntfy._header_value("12.40% @ ₹950 — DEV")
    assert enc.startswith("=?UTF-8?B?") and enc.endswith("?=")


def test_every_alert_write_touches_the_change_marker(monkeypatch):
    touched: list[int] = []
    monkeypatch.setattr(rules, "touch", touched.append)
    conns: list = []

    class Cur:
        rowcount = 1

        def fetchone(self):
            return {"id": 1, "user_id": 1, "filters": {}, "min_ask_ytm": 12,
                    "min_ticket": None, "enabled": True,
                    "created_at": NOW, "updated_at": NOW, "last_hit_at": None,
                    "isin": "INE000A01001", "min_bid_price": 1, "max_bid_ytm": None}

    class Conn:
        def execute(self, *a):
            conns.append(a[0])
            return Cur()

    class Ctx:
        def __enter__(self):
            return Conn()

        def __exit__(self, *exc):
            return False
    monkeypatch.setattr(rules.db, "_conn", lambda: Ctx())
    rules.create_rule(1, {"filters": {}, "min_ask_ytm": 12})
    rules.update_rule(1, 1, {"enabled": False})
    rules.delete_rule(1, 1)
    rules.put_target(1, "INE000A01001", {"min_bid_price": 1})
    rules.delete_target(1, "INE000A01001")
    rules.set_topic(1, "bondlens-x1y2z3")
    assert touched == [1] * 6


def test_recent_hits_derives_labels_for_hits_without_one(monkeypatch):
    rows = [
        {"id": 1, "source": "rule:4", "isin": "A", "side": "ask", "price": 997, "qty": 10, "ytm": 15.95,
         "exchange": "BSE", "trade_day": NOW.date(), "tick_at": None, "fired_at": NOW, "pushed_at": None,
         "push_ms": 49, "below_threshold": None, "rule_label": None, "issuer_name": "ICL", "nse_symbol": None,
         "bse_scrip_id": None, "r_filters": {"min_rating": "BBB-"}, "r_min_ask_ytm": 15, "r_min_ticket": None,
         "t_min_bid_price": None, "t_max_bid_ytm": None},
        {"id": 2, "source": "rule:2", "isin": "B", "side": "ask", "price": 1, "qty": 1, "ytm": None,
         "exchange": None, "trade_day": NOW.date(), "tick_at": None, "fired_at": NOW, "pushed_at": None,
         "push_ms": None, "below_threshold": None, "rule_label": None, "issuer_name": None, "nse_symbol": None,
         "bse_scrip_id": None, "r_filters": None, "r_min_ask_ytm": None, "r_min_ticket": None,
         "t_min_bid_price": None, "t_max_bid_ytm": None},
        {"id": 3, "source": "target", "isin": "C", "side": "bid", "price": 1015, "qty": 5, "ytm": 8.9,
         "exchange": "NSE", "trade_day": NOW.date(), "tick_at": None, "fired_at": NOW, "pushed_at": None,
         "push_ms": 30, "below_threshold": None, "rule_label": "stored", "issuer_name": None, "nse_symbol": None,
         "bse_scrip_id": None, "r_filters": None, "r_min_ask_ytm": None, "r_min_ticket": None,
         "t_min_bid_price": 1010, "t_max_bid_ytm": None},
    ]

    class Conn:
        def execute(self, sql, args):
            assert "a.trade_day = %s" in sql and len(args) == 4
            return self

        def fetchall(self):
            return rows

    class Ctx:
        def __enter__(self):
            return Conn()

        def __exit__(self, *exc):
            return False
    monkeypatch.setattr(rules.db, "_conn", lambda: Ctx())
    out = rules.recent_hits(1, today=NOW.date())
    assert [h["rule_label"] for h in out] == ["Ask YTM ≥ 15%, Rating ≥ BBB-", "Deleted rule #2", "stored"]
    assert all(not k.startswith(("r_", "t_")) for h in out for k in h)
    rules.recent_hits(1)   # no day filter: NULL day binds match every row


def test_arming_needs_budget_cap_and_kite(client, monkeypatch):
    c, store, _ = client
    rid = c.post("/api/alerts/rules", json=RULE).json()["id"]
    r = c.post(f"/api/alerts/rules/{rid}/auto-buy", json={"on": True})
    assert r.status_code == 400 and "budget" in r.json()["detail"]
    r = c.patch(f"/api/alerts/rules/{rid}", json={"buy_budget": 10000, "buy_daily_cap": 50000})
    assert r.status_code == 200 and r.json()["buy_budget"] == 10000
    assert c.post(f"/api/alerts/rules/{rid}/auto-buy", json={"on": True}).json()["auto_buy"] is True
    assert c.post("/api/alerts/disarm").json() == {"ok": True}
    assert c.get("/api/alerts").json()["rules"][0]["auto_buy"] is False
    monkeypatch.setattr("backend.app.kite_connect.token_for", lambda uid: None)
    assert c.post(f"/api/alerts/rules/{rid}/auto-buy", json={"on": True}).status_code == 409
    assert c.post("/api/alerts/rules/999/auto-buy", json={"on": False}).status_code == 404


def test_validate_rule_auto_buy_fields():
    with pytest.raises(rules.RuleError):
        rules.validate_rule({"min_ask_ytm": 12, "buy_budget": 1000})          # cap missing
    with pytest.raises(rules.RuleError):
        rules.validate_rule({"min_ask_ytm": 12, "buy_budget": 60000, "buy_daily_cap": 50000})
    v = rules.validate_rule({"min_ask_ytm": 12, "buy_budget": "10000", "buy_daily_cap": 50000})
    assert v["buy_budget"] == 10000.0 and v["buy_daily_cap"] == 50000.0


def test_blacklist_crud_is_per_user(client):
    c, store, current = client
    r = c.put("/api/alerts/blacklist/ine000a00001", json={"note": "thin book"})
    assert r.status_code == 200 and r.json()["isin"] == "INE000A00001" and r.json()["note"] == "thin book"
    assert c.put("/api/alerts/blacklist/nope", json={}).status_code == 400
    assert [e["isin"] for e in c.get("/api/alerts").json()["blacklist"]] == ["INE000A00001"]
    current["id"] = 2
    assert c.get("/api/alerts").json()["blacklist"] == []
    current["id"] = 1
    assert c.delete("/api/alerts/blacklist/INE000A00001").status_code == 200
    assert c.get("/api/alerts").json()["blacklist"] == []
