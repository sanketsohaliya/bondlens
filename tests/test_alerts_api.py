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

    def list_rules(self, user_id):
        return [r for r in self.rules.values() if r["user_id"] == user_id]

    def create_rule(self, user_id, payload):
        v = rules.validate_rule(payload)
        row = {**v, "id": self.next_id, "user_id": user_id, "created_at": NOW,
               "updated_at": NOW, "last_hit_at": None}
        self.rules[self.next_id] = row
        self.next_id += 1
        return row

    def update_rule(self, user_id, rule_id, payload):
        row = self.rules.get(rule_id)
        if row is None or row["user_id"] != user_id:
            raise rules.RuleError(404, "rule not found")
        v = rules.validate_rule({**row, **payload})
        row.update(v)
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
        row = {**v, "isin": isin, "user_id": user_id, "updated_at": NOW}
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

    def recent_hits(self, user_id, limit=200):
        return []

    def hits_today(self, user_id, today):
        return {"rule:1": 3}

    def read_heartbeat(self):
        return None


@pytest.fixture
def client(monkeypatch):
    store = FakeStore()
    for name in ("list_rules", "create_rule", "update_rule", "delete_rule", "list_targets",
                 "put_target", "delete_target", "get_topic", "set_topic", "recent_hits",
                 "hits_today", "read_heartbeat"):
        monkeypatch.setattr(rules, name, getattr(store, name))
    monkeypatch.setattr(queries, "bond_row", lambda isin: {"isin": isin} if isin.startswith("INE") else None)
    monkeypatch.setattr(ntfy, "push", lambda topic, title, body, **kw: store.pushed.append((topic, title)) or 12)
    current = {"id": 1}
    app.dependency_overrides[require_user] = lambda: current["id"]
    try:
        yield TestClient(app), store, current
    finally:
        app.dependency_overrides.pop(require_user, None)


RULE = {"name": "BBB 12%", "filters": {"min_rating": "BBB-", "min_years": 0.5},
        "min_ask_ytm": 12, "max_ticket": 200000}


def test_rule_lifecycle_and_overview(client):
    c, store, _ = client
    r = c.post("/api/alerts/rules", json=RULE)
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["id"] == 1 and rule["filters"] == {"min_rating": "BBB-", "min_years": 0.5}
    assert rule["min_qty"] is None and rule["enabled"] is True
    r = c.patch("/api/alerts/rules/1", json={"enabled": False, "max_price": 1050})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["max_price"] == 1050
    over = c.get("/api/alerts").json()
    assert [x["id"] for x in over["rules"]] == [1] and over["settings"] == {"ntfy_topic": None}
    assert over["targets"] == [] and over["recent"] == [] and over["watcher"] is None
    assert over["hits_today"] == {"rule:1": 3}
    assert c.delete("/api/alerts/rules/1").json() == {"ok": True}
    assert c.delete("/api/alerts/rules/1").status_code == 404


def test_rules_refuse_trade_history_filters_and_empty_conditions(client):
    c, _, _ = client
    r = c.post("/api/alerts/rules", json={**RULE, "filters": {"min_rating": "BBB-", "traded_within": 5, "min_ytm": 5}})
    assert r.status_code == 400 and "min_ytm, traded_within" in r.json()["detail"]
    r = c.post("/api/alerts/rules", json={"name": "x", "filters": {}, "max_ticket": 1000})
    assert r.status_code == 400 and "minimum YTM @ Ask or a maximum price" in r.json()["detail"]
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
    assert r.json() == {"ntfy_topic": "bondlens-opps-abc123"}
    assert c.post("/api/alerts/test").json() == {"ok": True}
    assert store.pushed == [("bondlens-opps-abc123", "BondLens alerts are wired")]
    assert c.put("/api/alerts/settings", json={"ntfy_topic": None}).json() == {"ntfy_topic": None}


def test_validate_rule_normalises_numbers_and_filters():
    v = rules.validate_rule({"name": " r ", "filters": {"min_rating": "aa-", "sector": ""},
                             "min_ask_ytm": "12.5", "min_qty": None})
    assert v["name"] == "r" and v["min_ask_ytm"] == 12.5 and v["min_qty"] is None
    assert v["filters"] == {"min_rating": "aa-"}
    with pytest.raises(rules.RuleError):
        rules.validate_rule({"name": "", "min_ask_ytm": 1})
    with pytest.raises(rules.RuleError):
        rules.validate_rule({"name": "x", "min_ask_ytm": "lots"})


def test_explorer_params_map_like_the_bonds_endpoint():
    p = rules.explorer_params({"min_rating": "BBB-", "min_years": 1, "traded_within": 5})
    assert p["min_rating_rank"] is not None and "min_rating" not in p
    assert p["min_years"] == 1 and "traded_within" not in p and p["limit"] == 100_000


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
            return {"id": 1, "user_id": 1, "name": "r", "filters": {}, "min_ask_ytm": 12,
                    "max_ticket": None, "max_price": None, "min_qty": None, "enabled": True,
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
    rules.create_rule(1, {"name": "r", "filters": {}, "min_ask_ytm": 12})
    rules.update_rule(1, 1, {"enabled": False})
    rules.delete_rule(1, 1)
    rules.put_target(1, "INE000A01001", {"min_bid_price": 1})
    rules.delete_target(1, "INE000A01001")
    rules.set_topic(1, "bondlens-x1y2z3")
    assert touched == [1] * 6
