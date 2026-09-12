"""Notification detection (pure functions) + API route tests.

The detectors in backend/notifications.py take plain data and return
alert dicts, so no database is needed. The API layer is faked like the
watchlist tests (CI has no Postgres).
"""
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import auth, db
from backend import notifications as nt
from backend.analytics import schedule
from backend.app import app

TODAY = date(2026, 1, 15)
ISIN = "INE000A00001"
LABEL = "ACME Finance"


def flow(due: date, amount: float | None = 50.0, event: str = "interest",
         **kw) -> schedule.Flow:
    return schedule.Flow(event=event, due_date=due,
                         record_date=kw.get("record_date"),
                         amount=amount, estimated=kw.get("estimated", False), payment_date=kw.get("payment_date"))


def raw(due: date, paid: date | None = None,
        reason: str | None = None) -> dict[str, Any]:
    return {"event": "Interest", "amount_payable": 50, "due_date": due, "actual_payment_date": paid,
            "reason_delay_payment": reason}


def lots(units: float = 1, purchase_date: date | None = None) -> list[dict]:
    return [{"units": units, "purchase_date": purchase_date}]


# --- rating_alerts ----------------------------------------------------------

def hist(rating_date: date, rating="AA-", prev="AA") -> list[dict]:
    return [
        {"agency": "CRISIL", "rating": prev,
         "rating_date": rating_date - timedelta(days=200)},
        {"agency": "CRISIL", "rating": rating, "rating_date": rating_date},
    ]


def test_rating_downgrade_alerts_warn():
    [a] = nt.rating_alerts(ISIN, LABEL, hist(TODAY - timedelta(days=3)), TODAY)
    assert a["kind"] == "rating"
    assert a["severity"] == "warn"
    assert "downgraded" in a["title"]
    assert "AA \u2192 AA-" in a["title"]
    assert a["dedupe_key"].startswith(f"rating:v2:{ISIN}:CRISIL:{TODAY - timedelta(days=3)}:")


def test_rating_upgrade_is_info():
    [a] = nt.rating_alerts(
        ISIN, LABEL, hist(TODAY - timedelta(days=3), rating="AA+", prev="AA"),
        TODAY)
    assert a["severity"] == "info"
    assert "upgraded" in a["title"]


def test_old_rating_change_is_silent():
    assert nt.rating_alerts(
        ISIN, LABEL, hist(TODAY - timedelta(days=45)), TODAY) == []


def test_unchanged_trail_is_silent():
    history = [{"agency": "CRISIL", "rating": "AA",
                "rating_date": TODAY - timedelta(days=5)}]
    assert nt.rating_alerts(ISIN, LABEL, history, TODAY) == []


# --- payment_alerts ---------------------------------------------------------

def test_upcoming_window_edges():
    flows = [flow(TODAY + timedelta(days=1)), flow(TODAY + timedelta(days=7)),
             flow(TODAY + timedelta(days=8))]
    alerts = nt.payment_alerts(ISIN, LABEL, lots(10), flows, [], TODAY)
    assert [a["kind"] for a in alerts] == ["pay_upcoming", "pay_upcoming"]


def test_upcoming_amount_scales_by_units():
    [a] = nt.payment_alerts(ISIN, LABEL, lots(10),
                            [flow(TODAY + timedelta(days=2), amount=41.5)],
                            [], TODAY)
    assert "\u20b9415" in a["title"]


def test_due_today():
    [a] = nt.payment_alerts(ISIN, LABEL, lots(), [flow(TODAY)], [], TODAY)
    assert a["kind"] == "pay_today"
    assert "Scheduled" in a["title"] and "15 Jan 2026" in a["title"]


def test_confirmed_on_time_is_info():
    d = TODAY - timedelta(days=10)
    [a] = nt.payment_alerts(ISIN, LABEL, lots(), [flow(d)],
                            [raw(d, paid=d)], TODAY)
    assert a["kind"] == "pay_confirmed"
    assert a["severity"] == "info"


def test_confirmed_late_is_warn_with_days():
    d = TODAY - timedelta(days=10)
    [a] = nt.payment_alerts(ISIN, LABEL, lots(), [flow(d)],
                            [raw(d, paid=d + timedelta(days=4))], TODAY)
    assert a["kind"] == "pay_confirmed"
    assert a["severity"] == "warn"
    assert "4 calendar days after" in a["body"]


def test_unconfirmed_past_payment_is_silent():
    # No actual_payment_date carries no signal: NSDL execution reporting
    # lags or is never filed for many issuers. Only issuer-filed delays warn.
    d = TODAY - timedelta(days=30)
    assert nt.payment_alerts(ISIN, LABEL, lots(), [flow(d)],
                             [raw(d)], TODAY) == []


def test_delay_reason_warns():
    d = TODAY - timedelta(days=1)
    [a] = nt.payment_alerts(ISIN, LABEL, lots(), [flow(d)],
                            [raw(d, reason="bank account mismatch")], TODAY)
    assert a["kind"] == "pay_overdue"
    assert "bank account mismatch" in a["body"]


def test_past_flow_without_raw_row_is_skipped():
    # Estimated schedules have no NSDL row to confirm against.
    d = TODAY - timedelta(days=30)
    assert nt.payment_alerts(ISIN, LABEL, lots(), [flow(d, estimated=True)],
                             [], TODAY) == []


def test_confirmation_lookback_bounded():
    d = TODAY - timedelta(days=nt.CONFIRM_LOOKBACK_DAYS + 1)
    assert nt.payment_alerts(ISIN, LABEL, lots(), [flow(d)],
                             [raw(d, paid=d)], TODAY) == []


def test_upcoming_with_passed_record_date_still_alerts():
    # Holder semantics: the record date passing does not hide a coupon
    # the holder is owed (this was the chart/notification mismatch).
    f = flow(TODAY + timedelta(days=3), record_date=TODAY - timedelta(days=5))
    [a] = nt.payment_alerts(ISIN, LABEL, lots(purchase_date=date(2025, 1, 1)),
                            [f], [], TODAY)
    assert a["kind"] == "pay_upcoming"


def test_lot_bought_after_record_date_is_silent():
    f = flow(TODAY + timedelta(days=3), record_date=TODAY - timedelta(days=5))
    bought_late = lots(purchase_date=TODAY - timedelta(days=1))
    assert nt.payment_alerts(ISIN, LABEL, bought_late, [f], [], TODAY) == []


def test_coupon_and_principal_alerts_do_not_share_a_dedupe_key():
    alerts = nt.payment_alerts(ISIN, LABEL, lots(), [flow(TODAY), flow(TODAY, 1000, "redemption")], [], TODAY)
    assert len(alerts) == len({a["dedupe_key"] for a in alerts}) == 2


def test_adjusted_payment_date_controls_upcoming_and_late_status():
    due = TODAY - timedelta(days=3)
    f = flow(due, payment_date=TODAY)
    report = {**raw(due, paid=TODAY), "payment_date": TODAY}
    [upcoming] = nt.payment_alerts(ISIN, LABEL, lots(), [f], [], TODAY - timedelta(days=1))
    assert upcoming["kind"] == "pay_upcoming"
    [paid] = nt.payment_alerts(ISIN, LABEL, lots(), [f], [report], TODAY)
    assert paid["kind"] == "pay_confirmed" and paid["severity"] == "info"


def test_coupon_confirmation_cannot_confirm_same_day_principal():
    due = TODAY - timedelta(days=3)
    alerts = nt.payment_alerts(ISIN, LABEL, lots(), [flow(due), flow(due, 1000, "redemption")],
                              [raw(due, paid=due), {"event": "Redemption", "amount_redeem": 1000, "due_date": due}], TODAY)
    assert len(alerts) == 1
    assert "coupon" in alerts[0]["title"].lower()


def test_conflicting_or_future_payment_reports_are_not_confirmed():
    due = TODAY - timedelta(days=3)
    assert nt.payment_alerts(ISIN, LABEL, lots(), [flow(due)],
                             [raw(due, paid=due), raw(due)], TODAY) == []
    assert nt.payment_alerts(ISIN, LABEL, lots(), [flow(due)],
                             [raw(due, paid=TODAY + timedelta(days=1))], TODAY) == []


def test_all_recent_agency_changes_are_reported():
    history = hist(TODAY - timedelta(days=2)) + [
        {"agency": "ICRA", "rating": "A", "rating_date": TODAY - timedelta(days=200)},
        {"agency": "ICRA", "rating": "AA", "rating_date": TODAY - timedelta(days=1)}]
    alerts = nt.rating_alerts(ISIN, LABEL, history, TODAY)
    assert len(alerts) == 2 and {a["severity"] for a in alerts} == {"info", "warn"}
    assert any("CRISIL" in a["dedupe_key"] for a in alerts)


def test_unknown_paid_amount_stays_unknown_and_future_filings_are_silent():
    record = {"payment_due_date": TODAY, "amount_to_be_paid": 1000, "amount_paid": None}
    [alert] = nt.default_alerts(ISIN, LABEL, [], [record], TODAY)
    assert "paid unknown" in alert["body"]
    assert nt.default_alerts(ISIN, LABEL, [{"verification_date": TODAY + timedelta(days=1)}], [], TODAY) == []


# --- default_alerts ---------------------------------------------------------

def test_recent_default_is_critical():
    rows = [{"verification_date": TODAY - timedelta(days=5),
             "default_details": "interest unpaid"}]
    [a] = nt.default_alerts(ISIN, LABEL, rows, [], TODAY)
    assert a["kind"] == "default"
    assert a["severity"] == "critical"
    assert "interest unpaid" in a["body"]


def test_recent_technical_delay_is_warn():
    rows = [{"payment_due_date": TODAY - timedelta(days=5),
             "update_date": TODAY - timedelta(days=3),
             "amount_to_be_paid": 100000, "amount_paid": 0,
             "failure_reason": "IFSC mismatch"}]
    [a] = nt.default_alerts(ISIN, LABEL, [], rows, TODAY)
    assert a["kind"] == "default"
    assert a["severity"] == "warn"
    assert "IFSC mismatch" in a["body"]


def test_old_filings_are_silent():
    old = TODAY - timedelta(days=nt.DEFAULT_WINDOW_DAYS + 1)
    defaults = [{"verification_date": old, "default_details": "x"}]
    tech = [{"payment_due_date": old, "update_date": old}]
    assert nt.default_alerts(ISIN, LABEL, defaults, tech, TODAY) == []


# --- anomaly_alerts ---------------------------------------------------------

def sessions(*prices: float, last: float,
             last_day: date = TODAY) -> list[tuple[date, float]]:
    days = [(last_day - timedelta(days=len(prices) - i), p)
            for i, p in enumerate(prices)]
    return days + [(last_day, last)]


def test_price_drop_alerts_warn():
    [a] = nt.anomaly_alerts(
        ISIN, LABEL, sessions(1000, 1001, 999, 1000, 1002, last=920), TODAY)
    assert a["kind"] == "anomaly"
    assert a["severity"] == "warn"
    assert "below" in a["title"]


def test_price_spike_is_info():
    [a] = nt.anomaly_alerts(
        ISIN, LABEL, sessions(1000, 1001, 999, 1000, 1002, last=1080), TODAY)
    assert a["severity"] == "info"
    assert "above" in a["title"]


def test_small_move_is_silent():
    assert nt.anomaly_alerts(
        ISIN, LABEL, sessions(1000, 1001, 999, 1000, 1002, last=1030),
        TODAY) == []


def test_too_few_sessions_is_silent():
    assert nt.anomaly_alerts(
        ISIN, LABEL, sessions(1000, 1001, last=900), TODAY) == []


def test_stale_last_trade_is_silent():
    old = TODAY - timedelta(days=nt.ANOMALY_STALE_DAYS + 1)
    assert nt.anomaly_alerts(
        ISIN, LABEL,
        sessions(1000, 1001, 999, 1000, 1002, last=920, last_day=old),
        TODAY) == []


# --- API routes -------------------------------------------------------------

class FakeStore:
    def __init__(self):
        self.rows = [
            {"id": 2, "kind": "rating", "severity": "warn", "isin": ISIN,
             "title": "t2", "body": "", "created_at": "2026-01-15T08:00:00Z",
             "read_at": None},
            {"id": 1, "kind": "pay_upcoming", "severity": "info", "isin": ISIN,
             "title": "t1", "body": "b", "created_at": "2026-01-14T08:00:00Z",
             "read_at": "2026-01-14T09:00:00Z"},
        ]

    def get_notifications(self, user_id: int, limit: int = 50, offset: int = 0):
        items = self.rows[offset:offset + limit]
        return {"items": items,
                "unread": sum(1 for r in self.rows if r["read_at"] is None)}

    def mark_notifications_read(self, user_id: int, ids: list[int]) -> int:
        n = 0
        for r in self.rows:
            if r["read_at"] is None and r["id"] in ids:
                r["read_at"] = "2026-01-15T10:00:00Z"
                n += 1
        return n


@pytest.fixture
def client(monkeypatch):
    store = FakeStore()
    for fn in ("get_notifications", "mark_notifications_read"):
        monkeypatch.setattr(db, fn, getattr(store, fn))
    app.dependency_overrides[auth.require_user] = lambda: 1
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_feed_route(client):
    r = client.get("/api/notifications")
    assert r.status_code == 200
    data = r.json()
    assert data["unread"] == 1
    assert [i["id"] for i in data["items"]] == [2, 1]


def test_mark_read_route(client):
    assert client.post("/api/notifications/read").json() == {"ok": True}
    assert client.get("/api/notifications").json()["unread"] == 1
    assert client.post("/api/notifications/read", json={"ids": [1]}).json() == {"ok": True}
    assert client.get("/api/notifications").json()["unread"] == 1
    assert client.post("/api/notifications/read", json={"ids": [2]}).json() == {"ok": True}
    assert client.get("/api/notifications").json()["unread"] == 0


def test_notification_feed_can_page_without_marking_unseen_rows(client):
    response = client.get("/api/notifications?limit=1&offset=1").json()
    assert [item["id"] for item in response["items"]] == [1]
    assert response["unread"] == 1
