from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from psycopg import OperationalError

from backend import operational_alerts as alerts
from scripts import healthcheck

NOW = datetime(2026, 9, 12, 10, tzinfo=UTC)


@pytest.fixture
def state(monkeypatch):
    value = {"active": False, "fingerprint": "", "last_notified_at": None}

    class Connection:
        def execute(self, sql, params):
            if sql.startswith("UPDATE"):
                value.update(active=params[0], fingerprint=params[1], last_notified_at=params[2])
            return self

        def fetchone(self):
            return dict(value)

    @contextmanager
    def connect():
        yield Connection()

    monkeypatch.setattr(alerts.db, "_conn", connect)
    return value


def test_identical_reports_are_deduplicated_with_daily_reminders_and_recovery(state):
    sent = []

    def sender(*args, **kwargs):
        sent.append(args)
        return True

    assert not alerts.report("health", [], sender, now=NOW)
    assert alerts.report("health", ["Prices stale"], sender, now=NOW)
    assert not alerts.report("health", ["Prices stale"], sender, now=NOW + timedelta(hours=1))
    assert alerts.report("health", ["Prices stale"], sender, now=NOW + timedelta(hours=25))
    assert alerts.report("health", [], sender, now=NOW + timedelta(hours=26))
    assert not alerts.report("health", [], sender, now=NOW + timedelta(hours=27))
    assert len(sent) == 3 and "recovered" in sent[-1][0]


def test_failed_delivery_of_a_changed_incident_remains_retryable(state):
    assert alerts.report("health", ["First problem"], lambda *a, **kw: True, now=NOW)
    assert not alerts.report("health", ["Different problem"], lambda *a, **kw: False, now=NOW + timedelta(hours=1))
    assert state["last_notified_at"] is None
    assert alerts.report("health", ["Different problem"], lambda *a, **kw: True, now=NOW + timedelta(hours=2))


def test_database_outage_does_not_prevent_operational_alert(monkeypatch):
    @contextmanager
    def unavailable():
        raise OperationalError("EXAMPLE_ONLY_CONNECTION_SECRET")
        yield

    monkeypatch.setattr(alerts.db, "_conn", unavailable)
    sent = []
    assert alerts.report("health", ["Check failed"], lambda *a, **kw: (sent.append(a), True)[1], now=NOW)
    assert "EXAMPLE_ONLY" not in str(sent)


def test_healthcheck_exception_is_reported_without_exception_text(monkeypatch, capsys):
    monkeypatch.setattr(healthcheck.sys, "argv", ["healthcheck.py"])
    monkeypatch.setattr(healthcheck.db, "close_pool", lambda: None)
    monkeypatch.setattr(healthcheck.health, "check", lambda: (_ for _ in ()).throw(RuntimeError("EXAMPLE_ONLY_SECRET")))
    reported = []
    monkeypatch.setattr(healthcheck.operational_alerts, "report", lambda name, problems, sender: reported.extend(problems))
    assert healthcheck.main() == 1
    assert reported and "RuntimeError" in reported[0]
    assert "EXAMPLE_ONLY" not in capsys.readouterr().out + str(reported)
