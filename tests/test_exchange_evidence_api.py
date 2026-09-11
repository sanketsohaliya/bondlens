from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend import auth, queries
from backend.app import app

ISIN = "INE148I07RZ0"


@pytest.fixture
def client():
    app.dependency_overrides[auth.require_user] = lambda: 1
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(auth.require_user, None)


def inputs():
    today = date.today()
    due = today + timedelta(days=16)
    return {
        "bond": {"isin": ISIN},
        "terms": {"face_value": 1000, "coupon_rate": 10, "redemption_date": due},
        "cashflows": [{"event": "Interest", "due_date": due, "record_date": today + timedelta(days=1), "amount_payable": 10},
                      {"event": "Redemption", "due_date": due, "record_date": today + timedelta(days=1), "amount_redeem": 1000}],
        "records": [{"id": 1, "raw_id": 1, "source": "nse_debt_announcements", "source_record_id": "test",
                     "category_raw": "Record Date Updates", "raw": {},
                     "headline": f"Nature of Payment - Interest Payment , Record date - {today:%d-%b-%Y} , Due date - {due:%d-%b-%Y} , ISIN - {ISIN}"}],
        "availability": [{"source": "nse_debt_announcements", "last_success_at": datetime.now(UTC), "latest_ok": True,
                          "target": {"active": True, "history_from": date(1990, 1, 1), "pending": [], "recent_pending": []}},
                         {"source": "bse_corporate_actions", "target": None}],
        "partial": False,
    }


def test_evidence_endpoint_is_authenticated():
    response = TestClient(app).get(f"/api/bonds/{ISIN}/cashflow-evidence")
    assert response.status_code == 401


def test_evidence_endpoint_returns_date_only_comparison(client, monkeypatch):
    calls = []

    def read(isin):
        calls.append(isin)
        return inputs()

    monkeypatch.setattr(queries, "cashflow_evidence_inputs", read, raising=False)
    response = client.get(f"/api/bonds/{ISIN.lower()}/cashflow-evidence")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["isin"] == ISIN
    assert body["as_of"] == date.today().isoformat()
    assert body["summary"]["differences"] == 1
    assert calls == [ISIN]
    assert all("amount" not in flow for flow in body["flows"])
    assert body["sources"][1]["status"] == "not_captured"


def test_unknown_bond_is_distinct_from_missing_evidence(client, monkeypatch):
    monkeypatch.setattr(queries, "cashflow_evidence_inputs", lambda _: None, raising=False)
    assert client.get(f"/api/bonds/{ISIN}/cashflow-evidence").status_code == 404


def test_invalid_or_excessive_windows_are_rejected_before_query(client, monkeypatch):
    def fail(_):
        raise AssertionError("invalid request should not reach database")

    monkeypatch.setattr(queries, "cashflow_evidence_inputs", fail, raising=False)
    today = date.today()
    for params in ({"from": str(today), "to": str(today - timedelta(days=1))},
                   {"from": str(today), "to": str(today + timedelta(days=367))},
                   {"from": "9999-12-31"}):
        assert client.get(f"/api/bonds/{ISIN}/cashflow-evidence", params=params).status_code == 422
