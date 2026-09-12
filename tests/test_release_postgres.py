import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import LiteralString, cast
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection, sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from backend import db, fills, operational_alerts
from backend.analytics import compute, valuation

pytestmark = pytest.mark.e2e
ISIN = "INE000TEST01"


@pytest.fixture(scope="module")
def database():
    url = os.environ.get("RELEASE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("RELEASE_TEST_DATABASE_URL must name a disposable loopback database")
    info = conninfo_to_dict(url)
    assert info.get("dbname") == "bondlens_release_test"
    assert info.get("host") in {"127.0.0.1", "localhost"}
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT to_regclass('public.bonds')").fetchone() == (None,)
    for migration in sorted((Path(__file__).resolve().parents[1] / "migrations").glob("*.sql")):
        with psycopg.connect(url) as conn:
            conn.execute(sql.SQL(cast(LiteralString, migration.read_text())))
    with ConnectionPool(
        url, min_size=1, max_size=4, connection_class=Connection[DictRow],
        kwargs={"row_factory": dict_row, "options": "-c statement_timeout=15000 -c lock_timeout=10000"},
    ) as pool:
        pool.wait(timeout=20)
        with pool.connection() as conn:
            conn.execute("INSERT INTO bonds (isin, created_at, first_seen, last_seen, updated_at) "
                         "VALUES (%s, now(), now(), now(), now())", (ISIN,))
            end = date.today() + timedelta(days=365)
            conn.execute("INSERT INTO bond_terms (isin, face_value, coupon_rate, allotment_date, redemption_date, "
                         "interest_payment_frequency, day_count_convention) VALUES (%s, 1000, 10, %s, %s, 'Annual', 'Actual/365')",
                         (ISIN, date.today(), end))
            conn.execute("INSERT INTO bond_cashflows (isin, event, due_date, amount_payable, amount_redeem) "
                         "VALUES (%s, 'Interest', %s, 100, NULL), (%s, 'Redemption', %s, NULL, 1000)", (ISIN, end, ISIN, end))
            conn.execute("INSERT INTO eod_trades (isin, trade_date, exchange, segment, price_basis, close, volume, turnover, num_trades) "
                         "VALUES (%s, %s, 'BSE', 'retail', 'dirty_rupees', 1000, 10, 10000, 1)", (ISIN, date.today()))
        yield pool


@pytest.fixture
def user(database, monkeypatch):
    @contextmanager
    def connect():
        with database.connection() as conn:
            yield conn

    monkeypatch.setattr(db, "_conn", connect)
    with db._conn() as conn:
        row = conn.execute("INSERT INTO users (google_sub, email) VALUES (%s, %s) RETURNING id",
                           (str(uuid4()), "release-test@example.invalid")).fetchone()
    assert row is not None
    return row["id"]


def order():
    return {"order_id": str(uuid4()), "isin": ISIN, "issuer_name": "Release test", "exchange": "BSE",
            "transaction_type": "BUY", "status": "COMPLETE", "quantity": 5, "filled_quantity": 5,
            "average_price": 1000, "order_timestamp": f"{date.today()} 10:00:00"}


def counts(user_id):
    values = []
    with db._conn() as conn:
        for table in ("user_portfolio_lots", "kite_fills", "user_notifications"):
            row = conn.execute(sql.SQL("SELECT count(*) AS n FROM {} WHERE user_id = %s").format(sql.Identifier(table)),
                               (user_id,)).fetchone()
            assert row is not None
            values.append(row["n"])
    return tuple(values)


@pytest.mark.parametrize("attempt", range(3))
def test_concurrent_fill_poll_and_sweep_create_exactly_one_lot(user, monkeypatch, attempt):
    barrier = Barrier(2)
    original = db.get_processed_order_ids

    def synchronized(user_id):
        result = original(user_id)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(db, "get_processed_order_ids", synchronized)
    payload = order()
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(fills.process_orders, user, [payload]) for _ in range(2)]
        assert sorted(future.result(timeout=20) for future in futures) == [0, 1]
    assert counts(user) == (1, 1, 1)
    with db._conn() as conn:
        linked = conn.execute("SELECT f.lot_id = l.id AS linked FROM kite_fills f "
                              "JOIN user_portfolio_lots l ON l.user_id = f.user_id WHERE f.user_id = %s", (user,)).fetchone()
    assert linked is not None and linked["linked"]


@pytest.mark.parametrize("stage", ["record_fill", "add_notification"])
def test_database_error_rolls_back_every_write_and_retry_is_idempotent(user, monkeypatch, stage):
    original = getattr(db, stage)
    payload = order()

    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        kwargs["conn"].execute("SELECT 1 / 0")

    with monkeypatch.context() as patch:
        patch.setattr(db, stage, fail_after_write)
        with pytest.raises(psycopg.errors.DivisionByZero):
            fills.process_orders(user, [payload])
    assert counts(user) == (0, 0, 0)
    assert fills.process_orders(user, [payload, payload]) == 1
    assert fills.process_orders(user, [payload]) == 0
    assert counts(user) == (1, 1, 1)


def test_lot_limit_does_not_discard_a_fill(user, monkeypatch):
    payload = order()
    with monkeypatch.context() as patch:
        patch.setattr(db, "MAX_LOTS", 0)
        assert fills.process_orders(user, [payload]) == 0
    assert counts(user) == (0, 0, 1)
    assert fills.process_orders(user, [payload]) == 1
    assert counts(user) == (1, 1, 2)


def test_migration_and_analytics_json_round_trip(user):
    assert compute.refresh() == 1
    with db._conn() as conn:
        row = conn.execute("SELECT valuation FROM bond_analytics WHERE isin = %s", (ISIN,)).fetchone()
    assert row is not None
    assert row["valuation"]["calculation_version"] == valuation.VERSION
    assert row["valuation"]["as_of"] == str(date.today())
    assert row["valuation"]["ytm"] == 10
    assert row["valuation"]["cashflows_complete"]


def test_mark_read_is_scoped_to_selected_ids_and_user(user):
    with db._conn() as conn:
        row = conn.execute("INSERT INTO users (google_sub, email) VALUES (%s, %s) RETURNING id",
                           (str(uuid4()), "other-release-test@example.invalid")).fetchone()
        assert row is not None
        other = row["id"]
    for owner, key in ((user, "one"), (user, "two"), (other, "other")):
        db.add_notification(owner, "rating", "info", ISIN, key, "", key)
    mine = db.get_notifications(user)["items"]
    foreign = db.get_notifications(other)["items"][0]["id"]
    assert db.mark_notifications_read(user, []) == 0
    assert db.mark_notifications_read(user, [mine[0]["id"], foreign]) == 1
    assert db.get_notifications(user)["unread"] == 1
    assert db.get_notifications(other)["unread"] == 1


def test_readonly_deployment_verifier_against_native_database(user, monkeypatch):
    import json

    from backend.app import app
    from scripts import verify_deployment

    compute.refresh()
    monkeypatch.setattr(db, "DATABASE_URL", os.environ["RELEASE_TEST_DATABASE_URL"])
    monkeypatch.setattr(verify_deployment, "fetch", lambda url: json.dumps(app.openapi()))
    verify_deployment.verify_database()


def test_operational_incident_deduplication_and_recovery(user):
    name, now, sent = str(uuid4()), datetime.now(UTC), []

    def sender(*args, **kwargs):
        sent.append(args)
        return True

    assert operational_alerts.report(name, ["Test incident"], sender, now=now)
    assert not operational_alerts.report(name, ["Test incident"], sender, now=now + timedelta(hours=1))
    assert operational_alerts.report(name, [], sender, now=now + timedelta(hours=2))
    assert len(sent) == 2 and "recovered" in sent[-1][0]
