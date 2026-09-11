import gzip
import json
import os
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import LiteralString, cast
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection, sql
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

pytestmark = pytest.mark.e2e

ISIN = "INE123A07001"
DAY = date(2026, 8, 1)
TEXT = '{"seq_id": "same", "amount": 1234567890.12345678901234567890, "unknown": null}'


@pytest.fixture
def storage():
    url = os.environ.get("CA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("CA_TEST_DATABASE_URL must explicitly name a disposable nonproduction database")
    from backend.corporate_action_store import Store

    schema = "ca_test_" + uuid4().hex
    admin = psycopg.connect(url, autocommit=True, connect_timeout=10)
    created = False
    pool = None
    try:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        created = True
        options = f"-c search_path={schema} -c statement_timeout=30000 -c lock_timeout=5000"
        pool = ConnectionPool(
            url, min_size=1, max_size=2, connection_class=Connection[DictRow],
            kwargs={"row_factory": dict_row, "connect_timeout": 10, "options": options},
            open=True,
        )
        pool.wait(timeout=15)

        @contextmanager
        def connect():
            assert pool is not None
            with pool.connection() as conn:
                yield conn

        def lock_connect():
            return Connection[DictRow].connect(url, autocommit=True, row_factory=dict_row,
                                               connect_timeout=10, options=options)

        with connect() as conn:
            migration = Path(__file__).resolve().parents[1] / "migrations" / "0018_corporate_actions.sql"
            conn.execute(sql.SQL(cast(LiteralString, migration.read_text())))
        yield Store(connect=connect, lock_connect=lock_connect), connect
    finally:
        if pool is not None:
            pool.close()
        if created:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


def occurrence(text=TEXT, position=0, method="explicit_isin", evidence=None):
    return {
        "position": position,
        "text": text,
        "associations": [{"isin": ISIN, "method": method, "evidence": evidence or {"path": "$.isin"}}],
    }


def fetch(store, source="nse", mode="recent", records=None, meta=None, spec=None):
    run_id = store.start_run(source, mode, {"scope": {"isins": [ISIN]}})
    fetch_id = store.start_fetch(run_id, source, DAY, DAY, spec or {
        "endpoint": "https://example.test/actions",
        "request_params": {"from": DAY.isoformat()},
        "request_key": "test-request",
        "scope": {"isins": [ISIN], "policy": "v1"},
        "attempt": 1,
        "parent_fetch_id": None,
        "kind": mode,
    })
    store.save_response(fetch_id, meta or {"fetch_status": "success", "http_status": 200},
                        records if records is not None else [occurrence()])
    return run_id, fetch_id


def extract(source, native):
    assert source in {"nse", "bse"}
    return {
        "source_record_id": native.get("seq_id"),
        "headline": native.get("headline"),
        "category_raw": "Unfamiliar category",
        "extractor_version": "test-v1",
        "metadata_status": "ok",
        "warnings": [],
    }


def test_migration_is_additive_and_fetch_starts_pending(storage):
    store, connect = storage
    run = store.start_run("nse", "backfill", {"frozen": [ISIN]})
    spec = {"scope": {"isins": [ISIN]}, "kind": "history", "attempt": 2}
    fetch_id = store.start_fetch(run, "nse", DAY, DAY, spec)
    row = store.fetches("nse")[0]
    assert row["id"] == fetch_id
    assert row["mode"] == "backfill"
    assert row["status"] == "running"
    assert row["index_status"] == "pending"
    assert row["spec"] == spec
    assert row["window_from"] == row["window_to"] == DAY
    assert row["started_at"] is not None
    assert store.pending_fetches() == []
    with connect() as conn:
        tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()").fetchall()
    assert {row["tablename"] for row in tables} == {
        "corporate_action_runs", "corporate_action_fetches", "corporate_action_raw",
        "corporate_actions", "corporate_action_isins", "corporate_action_targets",
    }


def test_exact_raw_dedupe_revisions_and_occurrence_manifest(storage):
    store, connect = storage
    records = [occurrence(position=4), occurrence(position=8, evidence={"path": "$.text"})]
    _, first = fetch(store, records=records)
    assert store.index_fetch(first, extract) == 2
    _, repeated = fetch(store)
    assert store.index_fetch(repeated, extract) == 1
    revised = TEXT.replace('"unknown": null', '"unknown": {"changed": true}')
    _, revision = fetch(store, records=[occurrence(text=revised)])
    assert store.index_fetch(revision, extract) == 1
    with connect() as conn:
        raws = conn.execute("SELECT * FROM corporate_action_raw ORDER BY id").fetchall()
        actions = conn.execute("SELECT *, raw ->> 'amount' AS amount_text FROM corporate_actions ORDER BY id").fetchall()
        manifest = conn.execute("SELECT manifest FROM corporate_action_fetches WHERE id = %s", (first,)).fetchone()
        links = conn.execute("SELECT * FROM corporate_action_isins ORDER BY action_id").fetchall()
    assert len(raws) == len(actions) == len(links) == 2
    assert gzip.decompress(raws[0]["content"]).decode() == TEXT
    assert raws[0]["size_bytes"] == len(TEXT.encode())
    assert actions[0]["amount_text"] == "1234567890.12345678901234567890"
    assert actions[0]["raw"]["unknown"] is None
    assert actions[1]["raw"]["unknown"] == {"changed": True}
    assert actions[0]["source_record_id"] == actions[1]["source_record_id"] == "same"
    assert actions[0]["first_fetch_id"] == first
    assert actions[0]["last_fetch_id"] == repeated
    assert manifest is not None
    assert [item["position"] for item in manifest["manifest"]] == [4, 8]
    assert manifest["manifest"][0]["raw_id"] == manifest["manifest"][1]["raw_id"]
    assert [item["associations"] for item in manifest["manifest"]] == [item["associations"] for item in records]
    assert all("text" not in item for item in manifest["manifest"])
    assert store.pending_fetches() == []
    assert len(store.fetches("nse")) == 3


def test_failed_extractor_rolls_back_catalogue_but_evidence_replays(storage):
    store, connect = storage
    run, fetch_id = fetch(store, records=[occurrence(), occurrence(TEXT.replace('"same"', '"second"'), 1)])
    calls = []

    def fail_second(source, native):
        calls.append(native["seq_id"])
        if len(calls) == 2:
            raise ValueError("private native payload must not be persisted as an error")
        return extract(source, native)

    with pytest.raises(ValueError):
        store.index_fetch(fetch_id, fail_second)
    with connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM corporate_action_raw").fetchone()["n"] == 2
        assert conn.execute("SELECT count(*) AS n FROM corporate_actions").fetchone()["n"] == 0
        row = conn.execute("SELECT * FROM corporate_action_fetches WHERE id = %s", (fetch_id,)).fetchone()
    assert row["status"] == "success"
    assert row["index_status"] in {"pending", "failed"}
    assert "private native payload" not in str(row)
    assert [item["id"] for item in store.pending_fetches("nse", run)] == [fetch_id]
    assert store.pending_fetches("bse", run) == []
    assert store.pending_fetches("nse", run + 100) == []
    assert store.index_fetch(fetch_id, extract) == 2
    assert store.index_fetch(fetch_id, extract) == 2
    assert store.pending_fetches() == []


def test_old_replay_never_regresses_observation_ids_or_timestamps(storage):
    store, connect = storage
    _, older = fetch(store)
    _, newer = fetch(store)
    old_time = datetime(2020, 1, 1, tzinfo=UTC)
    new_time = datetime(2021, 1, 1, tzinfo=UTC)
    with connect() as conn:
        conn.execute("UPDATE corporate_action_fetches SET started_at = %s WHERE id = %s", (old_time, older))
        conn.execute("UPDATE corporate_action_fetches SET started_at = %s WHERE id = %s", (new_time, newer))
    store.index_fetch(newer, extract)
    store.index_fetch(older, extract)
    store.index_fetch(newer, extract)
    store.index_fetch(older, extract)
    with connect() as conn:
        row = conn.execute("SELECT * FROM corporate_actions").fetchone()
    assert row["first_fetch_id"] == older
    assert row["last_fetch_id"] == newer
    assert row["first_seen_at"] == old_time
    assert row["last_seen_at"] == new_time


def test_safe_empty_and_failed_response_metadata_without_mixed_payload(storage):
    store, connect = storage
    run, empty = fetch(store, records=[], meta={
        "fetch_status": "empty", "native_count": 2, "exclusion_counts": {"equity": 2},
        "body_sha256": "a" * 64, "body_size_bytes": 400,
        "body": "PRIVATE EQUITY", "records": [{"equity": "PRIVATE EQUITY"}],
    })
    _, failed = fetch(store, records=[], meta={"fetch_status": "invalid", "http_status": 200, "error": "invalid_envelope"})
    assert [row["id"] for row in store.pending_fetches(run_id=run)] == [empty]
    assert store.index_fetch(empty, extract) == 0
    with connect() as conn:
        rows = conn.execute("SELECT * FROM corporate_action_fetches ORDER BY id").fetchall()
        assert conn.execute("SELECT count(*) AS n FROM corporate_action_raw").fetchone()["n"] == 0
    assert "PRIVATE EQUITY" not in str(rows)
    assert rows[0]["meta"]["exclusion_counts"] == {"equity": 2}
    assert rows[1]["id"] == failed
    assert rows[1]["index_status"] == "not_applicable"
    with pytest.raises(ValueError):
        store.index_fetch(failed, extract)
    with pytest.raises(ValueError):
        store.save_response(empty, {"fetch_status": "success"}, [occurrence()])


def test_extractor_receives_decimal_without_float_roundtrip(storage):
    store, _ = storage
    _, fetch_id = fetch(store)

    def precise(source, native):
        assert native["amount"] == Decimal("1234567890.12345678901234567890")
        return extract(source, native)

    assert store.index_fetch(fetch_id, precise) == 1


def test_record_scope_and_source_participate_in_raw_identity(storage):
    store, connect = storage
    for source, scope in [("nse", ""), ("nse", "alternate"), ("bse", "")]:
        _, fetch_id = fetch(store, source=source, spec={"record_scope": scope})
        store.index_fetch(fetch_id, extract)
    with connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM corporate_action_raw").fetchone()["n"] == 3
        assert conn.execute("SELECT count(*) AS n FROM corporate_actions").fetchone()["n"] == 3


def test_target_upsert_preserves_enrollment_and_captured_history(storage):
    store, connect = storage
    _, fetch_id = fetch(store)
    store.index_fetch(fetch_id, extract)
    first_seen = datetime(2020, 1, 1, tzinfo=UTC)
    data = {
        "mapping_hash": "v1", "mapping": {"codes": ["12345"]}, "active": True,
        "first_seen_at": first_seen, "last_seen_at": first_seen,
        "last_progress_at": None, "history_from": date(2005, 1, 1), "history_to": DAY,
        "pending": [{"from": "2005-01-01", "to": DAY.isoformat()}],
        "state": "pending", "blocked_reason": None, "generation": 1,
        "last_active_date": DAY,
    }
    store.save_target("nse", ISIN, data)
    assert store.load_targets("nse")[ISIN]["mapping"] == data["mapping"]
    store.save_target("nse", ISIN, {"state": "complete", "pending": []})
    store.save_target("nse", ISIN, {"active": False, "state": "paused", "first_seen_at": datetime.now(UTC)})
    store.save_target("bse", ISIN, {"state": "blocked", "blocked_reason": "missing_mapping"})
    target = store.load_targets("nse")[ISIN]
    assert target["first_seen_at"] == first_seen
    assert target["history_from"] == data["history_from"]
    assert target["mapping_hash"] == "v1"
    assert target["pending"] == []
    assert not target["active"]
    assert target["state"] == "paused"
    assert store.load_targets("bse")[ISIN]["generation"] == 1
    store.save_target("nse", ISIN, {"active": True, "state": "partial", "generation": 2, "mapping_hash": "v2"})
    store.index_fetch(fetch_id, extract)
    with connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM corporate_actions").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM corporate_action_isins").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM corporate_action_targets").fetchone()["n"] == 2


def test_source_locks_use_separate_sessions_and_release_after_exception(storage):
    store, _ = storage
    with store.lock("nse") as acquired:
        assert acquired
        with store.lock("nse") as contended:
            assert not contended
        with store.lock("bse") as independent:
            assert independent
    with pytest.raises(ValueError), store.lock("nse") as acquired:
        assert acquired
        raise ValueError("test")
    with store.lock("nse") as reacquired:
        assert reacquired
    with store.lock("nse") as acquired:
        assert acquired
        with store.lock("nse") as contended:
            assert not contended
    assert store.fetches("nse") == []


def test_snapshot_is_payload_free_recent_freshness_not_backfill(storage):
    store, connect = storage
    recent, first = fetch(store)
    store.index_fetch(first, extract)
    store.finish_run(recent, True, {"accepted": 1})
    history, _ = fetch(store, mode="backfill")
    store.finish_run(history, True, {"accepted": 1})
    failed = store.start_run("nse", "recent", {})
    store.finish_run(failed, False, {"error": "timeout"})
    running = store.start_run("bse", "recent", {})
    store.save_target("nse", ISIN, {"state": "partial", "pending": [{"from": "2005-01-01", "to": "2005-01-14"}]})
    store.save_target("bse", ISIN, {"state": "blocked"})
    snapshot = store.snapshot()
    assert snapshot["nse"]["last_recent_success"] is not None
    assert snapshot["nse"]["latest_recent"]["id"] == failed
    assert snapshot["nse"]["latest_recent"]["state"] == "failed"
    assert snapshot["nse"]["pending_index"] == 1
    assert snapshot["nse"]["rows"] == snapshot["nse"]["raw_rows"] == 1
    assert snapshot["nse"]["raw_bytes"] == len(TEXT.encode())
    assert snapshot["nse"]["compressed_bytes"] > 0
    assert snapshot["nse"]["target_states"]["partial"] == 1
    assert snapshot["nse"]["remaining_history_windows"] == 1
    assert snapshot["bse"]["last_recent_success"] is None
    assert snapshot["bse"]["latest_recent"]["id"] == running
    assert snapshot["bse"]["latest_recent"]["state"] == "running"
    assert snapshot["bse"]["running_runs"] == 1
    assert snapshot["bse"]["target_states"]["blocked"] == 1
    serialized = json.dumps(snapshot, default=str)
    assert ISIN not in serialized
    assert "unknown" not in serialized
    assert "example.test" not in serialized
    with connect() as conn:
        run = conn.execute("SELECT * FROM corporate_action_runs WHERE id = %s", (recent,)).fetchone()
        assert run["ok"] is True
        assert run["summary"] == {"accepted": 1}
        assert snapshot["nse"]["last_recent_success"] == run["finished_at"]
        assert conn.execute("SELECT count(*) AS n FROM corporate_action_runs").fetchone()["n"] == 4


def test_reindex_failure_marks_previously_indexed_fetch_replayable(storage):
    store, connect = storage
    _, fetch_id = fetch(store)
    store.index_fetch(fetch_id, extract)

    def fail(source, native):
        raise RuntimeError("private payload")

    with pytest.raises(RuntimeError, match="private payload"):
        store.index_fetch(fetch_id, fail)
    assert [row["id"] for row in store.pending_fetches()] == [fetch_id]
    with connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM corporate_actions").fetchone()["n"] == 1
    store.index_fetch(fetch_id, extract)
    assert store.pending_fetches() == []


def test_adapter_metadata_names_are_projected(storage):
    store, connect = storage
    from backend.corporate_action_sources import SOURCES, metadata

    text = '{"scrip_code": "123456", "long_name": "Test bond", "purpose": "Unknown", "attachment_url": "https://example.test/notice.pdf"}'
    _, fetch_id = fetch(store, source=SOURCES[1], records=[occurrence(text)])
    assert store.index_fetch(fetch_id, metadata) == 1
    with connect() as conn:
        row = conn.execute("SELECT * FROM corporate_actions").fetchone()
    assert row["scrip_code"] == "123456"
    assert row["issuer_name"] == "Test bond"
    assert row["category_raw"] == "Unknown"
    assert row["attachment_urls"] == ["https://example.test/notice.pdf"]


def test_coverage_checkpoint_only_indexes_successes_and_is_idempotent(storage):
    store, _ = storage
    _, pending = fetch(store)
    assert store.coverage_fetches("nse") == []
    store.index_fetch(pending, extract)
    ready = store.coverage_fetches("nse")
    assert [row["id"] for row in ready] == [pending]
    assert ready[0]["coverage_applied_at"] is None
    assert ready[0]["resolved_at"] is None
    assert "manifest" not in ready[0]
    assert store.coverage_fetches("bse") == []
    store.mark_coverage(pending)
    timestamp = store.fetches("nse")[0]["coverage_applied_at"]
    assert timestamp is not None
    store.mark_coverage(pending)
    assert store.fetches("nse")[0]["coverage_applied_at"] == timestamp
    assert store.coverage_fetches("nse") == []
    _, empty = fetch(store, records=[], meta={"fetch_status": "empty"})
    store.index_fetch(empty, extract)
    assert [row["id"] for row in store.coverage_fetches("nse")] == [empty]
    _, failed = fetch(store, records=[], meta={"fetch_status": "invalid"})
    with pytest.raises(ValueError):
        store.mark_coverage(failed)


def test_unfinished_requests_choose_latest_and_ignore_completed_attempts(storage):
    store, _ = storage
    spec = {"kind": "recent", "request_key": "same-key"}
    _, first = fetch(store, records=[], meta={"fetch_status": "failed"}, spec=spec)
    _, second = fetch(store, records=[], meta={"fetch_status": "invalid"}, spec=spec)
    run = store.start_run("nse", "recent", {})
    running = store.start_fetch(run, "nse", DAY, DAY, spec)
    assert first < second < running
    unfinished = store.unfinished("nse")
    assert [row["id"] for row in unfinished] == [running]
    assert "manifest" not in unfinished[0]
    assert store.unfinished("bse") == []
    assert store.unfinished("nse", "history") == []
    assert not store.completed("nse", "same-key")
    _, success = fetch(store, spec=spec)
    assert not store.completed("nse", "same-key")
    assert [row["id"] for row in store.unfinished("nse")] == [running]
    store.index_fetch(success, extract)
    assert store.completed("nse", "same-key")
    assert not store.completed("bse", "same-key")
    assert store.unfinished("nse") == []
    store.resolve_request("nse", "same-key")
    timestamps = {row["id"]: row["resolved_at"] for row in store.fetches("nse")}
    assert all(timestamps.values())
    store.resolve_request("nse", "same-key")
    assert {row["id"]: row["resolved_at"] for row in store.fetches("nse")} == timestamps


def test_split_resolution_satisfies_request_without_conflating_other_scopes(storage):
    store, _ = storage
    spec = {"kind": "history", "request_key": "split-root"}
    _, root = fetch(store, records=[], meta={"fetch_status": "split"}, spec=spec)
    _, other = fetch(store, records=[], meta={"fetch_status": "incomplete"},
                     spec={"kind": "history", "request_key": "other-scope"})
    assert {row["id"] for row in store.unfinished("nse", "history")} == {root, other}
    assert not store.completed("nse", "split-root")
    store.resolve_request("nse", "split-root")
    assert store.completed("nse", "split-root")
    assert [row["id"] for row in store.unfinished("nse", "history")] == [other]
    assert not store.completed("nse", "other-scope")
    assert store.coverage_fetches("nse") == []
    store.resolve_request("nse", "other-scope")
    assert not store.completed("nse", "other-scope")
    assert store.unfinished("nse", "history") == []


def test_bulk_target_upsert_is_atomic_and_preserves_unspecified_fields(storage):
    store, _ = storage
    first_seen = datetime(2020, 1, 1, tzinfo=UTC)
    second = "INE123A07002"
    store.save_targets("nse", {
        ISIN: {"mapping_hash": "v1", "first_seen_at": first_seen, "state": "pending"},
        second: {"mapping": {"codes": ["123456"]}, "state": "blocked", "blocked_reason": "missing_mapping"},
    })
    store.save_targets("nse", {
        ISIN: {"state": "complete", "first_seen_at": datetime.now(UTC)},
        second: {"active": False},
    })
    rows = store.load_targets("nse")
    assert len(rows) == 2
    assert rows[ISIN]["mapping_hash"] == "v1"
    assert rows[ISIN]["first_seen_at"] == first_seen
    assert rows[ISIN]["state"] == "complete"
    assert rows[second]["state"] == "blocked"
    assert not rows[second]["active"]
    with pytest.raises(psycopg.errors.NotNullViolation):
        store.save_targets("nse", {ISIN: {"state": "partial"}, second: {"state": None}})
    assert store.load_targets("nse")[ISIN]["state"] == "complete"
    assert store.load_targets("bse") == {}
    store.save_targets("nse", {})
    assert len(store.load_targets("nse")) == 2


def test_snapshot_tracks_stuck_runs_unresolved_recent_and_blocked_reasons(storage):
    store, connect = storage
    from backend.corporate_action_sources import SOURCES

    source = SOURCES[0]
    initial = store.snapshot()
    assert set(initial) == set(SOURCES)
    assert initial[source]["stuck_runs"] == initial[source]["unfinished_recent"] == 0
    old = store.start_run(source, "recent", {})
    store.start_run(source, "recent", {})
    with connect() as conn:
        conn.execute("UPDATE corporate_action_runs SET run_at = now() - interval '4 hours' WHERE id = %s", (old,))
    spec = {"kind": "recent", "request_key": "retry-me"}
    fetch(store, source=source, records=[], meta={"fetch_status": "failed"}, spec=spec)
    fetch(store, source=source, records=[], meta={"fetch_status": "invalid"}, spec=spec)
    fetch(store, source=source, records=[], meta={"fetch_status": "incomplete"},
          spec={"kind": "history", "request_key": "history"})
    store.save_targets(source, {
        ISIN: {"state": "blocked", "blocked_reason": "missing_mapping"},
        "INE123A07002": {"state": "blocked", "blocked_reason": "missing_mapping"},
    })
    snapshot = store.snapshot()[source]
    assert snapshot["stuck_runs"] == 1
    assert snapshot["unfinished_recent"] == 1
    assert snapshot["blocked_reasons"] == {"missing_mapping": 2}
    store.resolve_request(source, "retry-me")
    store.finish_run(old, False, {})
    snapshot = store.snapshot()[source]
    assert snapshot["stuck_runs"] == 0
    assert snapshot["unfinished_recent"] == 0


def test_later_refresh_failure_overrides_old_success_and_unfinished_is_fifo(storage):
    store, _ = storage
    spec = {"kind": "recent", "request_key": "z-older"}
    _, success = fetch(store, records=[], meta={"fetch_status": "empty"}, spec=spec)
    store.index_fetch(success, extract)
    assert store.completed("nse", "z-older")
    _, failure = fetch(store, records=[], meta={"fetch_status": "failed"}, spec=spec)
    assert not store.completed("nse", "z-older")
    _, newer = fetch(store, records=[], meta={"fetch_status": "incomplete"},
                     spec={"kind": "recent", "request_key": "a-newer"})
    assert [row["id"] for row in store.unfinished("nse")] == [failure, newer]
    assert store.snapshot()["nse"]["unfinished_recent"] == 2
    run = store.start_run("nse", "recent", {})
    running = store.start_fetch(run, "nse", DAY, DAY, spec)
    assert not store.completed("nse", "z-older")
    assert [row["id"] for row in store.unfinished("nse")] == [newer, running]
    store.save_response(running, {"fetch_status": "empty"}, [])
    assert not store.completed("nse", "z-older")
    store.index_fetch(running, extract)
    assert store.completed("nse", "z-older")
    assert [row["id"] for row in store.unfinished("nse")] == [newer]
    _, split = fetch(store, records=[], meta={"fetch_status": "split"}, spec=spec)
    assert not store.completed("nse", "z-older")
    store.resolve_request("nse", "z-older")
    assert store.completed("nse", "z-older")
    _, later = fetch(store, records=[], meta={"fetch_status": "invalid"}, spec=spec)
    assert later > split
    assert not store.completed("nse", "z-older")
    assert [row["id"] for row in store.unfinished("nse")] == [newer, later]


def test_deferred_queue_reuses_unresolved_attempts_and_reopens_after_success(storage):
    store, _ = storage
    spec = {"request_key": "queued", "scope": {"isins": [ISIN]}}
    run = store.start_run("nse", "recent", {})
    queued = store.queue_fetch(run, "nse", DAY, DAY, spec)
    row = store.unfinished("nse")[0]
    assert row["id"] == queued
    assert row["status"] == "pending"
    assert row["spec"]["kind"] == "recent"
    assert row["spec"]["scope"] == spec["scope"]
    assert not store.completed("nse", "queued")
    assert store.snapshot()["nse"]["unfinished_recent"] == 1
    assert store.pending_fetches("nse") == []
    second_run = store.start_run("nse", "recent", {})
    assert store.queue_fetch(second_run, "nse", DAY, DAY, spec) == queued
    assert len(store.fetches("nse")) == 1
    actual_spec = {**spec, "kind": "recent"}
    for status in ("failed", "invalid", "incomplete", "split"):
        actual = store.start_fetch(second_run, "nse", DAY, DAY, actual_spec)
        assert store.queue_fetch(second_run, "nse", DAY, DAY, spec) == actual
        store.save_response(actual, {"fetch_status": status}, [])
        assert store.queue_fetch(second_run, "nse", DAY, DAY, spec) == actual
    success = store.start_fetch(second_run, "nse", DAY, DAY, actual_spec)
    store.save_response(success, {"fetch_status": "empty"}, [])
    store.index_fetch(success, extract)
    assert store.completed("nse", "queued")
    reopened = store.queue_fetch(second_run, "nse", DAY, DAY, spec)
    assert reopened > success
    assert not store.completed("nse", "queued")
    assert [row["id"] for row in store.unfinished("nse")] == [reopened]
    with pytest.raises(ValueError):
        store.queue_fetch(run, "nse", DAY, DAY, {})


def test_queue_claim_is_atomic_across_connections(storage):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store, _ = storage
    run = store.start_run("nse", "recent", {})
    barrier = Barrier(2)

    def queue():
        barrier.wait(timeout=30)
        return store.queue_fetch(run, "nse", DAY, DAY, {"request_key": "concurrent-queue"})

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(queue) for _ in range(2)]
        ids = [future.result(timeout=60) for future in futures]
    assert ids[0] == ids[1]
    assert len(store.fetches("nse")) == 1


def test_recent_target_obligations_are_independent_from_history(storage):
    store, _ = storage
    store.save_target("nse", ISIN, {"state": "complete"})
    assert store.load_targets("nse")[ISIN]["recent_pending"] == []
    recent = [{"from": "2026-07-25", "to": "2026-08-01"}]
    history = [{"from": "2010-01-01", "to": "2010-01-14"}]
    store.save_targets("nse", {ISIN: {"recent_pending": recent, "pending": history}})
    store.save_target("nse", ISIN, {"pending": [], "state": "complete"})
    target = store.load_targets("nse")[ISIN]
    assert target["recent_pending"] == recent
    assert target["pending"] == []
    store.save_targets("nse", {ISIN: {"recent_pending": []}})
    assert store.load_targets("nse")[ISIN]["recent_pending"] == []


def test_history_configuration_uses_latest_global_run_not_targets_or_scoped_runs(storage):
    store, _ = storage
    assert store.history_start("nse") is None
    store.start_run("nse", "backfill", {"configure_history": True, "history_start": "2005-01-01"})
    assert store.history_start("nse") == date(2005, 1, 1)
    store.start_run("nse", "backfill", {"configure_history": False, "history_start": "2000-01-01"})
    store.start_run("nse", "recent", {"history_start": "1990-01-01"})
    store.save_target("nse", ISIN, {"history_from": date(1980, 1, 1), "active": False})
    assert store.history_start("nse") == date(2005, 1, 1)
    store.start_run("bse", "backfill", {"configure_history": True, "history_start": "2010-01-01"})
    assert store.history_start("bse") == date(2010, 1, 1)
    store.start_run("nse", "backfill", {"configure_history": True, "history_start": "2003-01-01"})
    assert store.history_start("nse") == date(2003, 1, 1)
    store.start_run("nse", "backfill", {"configure_history": True, "history_start": None})
    assert store.history_start("nse") is None


def test_manual_reindex_upgrades_metadata_but_preserves_original_admission(storage):
    store, connect = storage
    run, fetch_id = fetch(store)
    store.index_fetch(fetch_id, extract)
    assert store.pending_fetches() == []
    assert [row["id"] for row in store.pending_fetches("nse", run, reindex=True)] == [fetch_id]
    assert store.pending_fetches("bse", run, reindex=True) == []
    assert store.pending_fetches("nse", run + 100, reindex=True) == []
    with connect() as conn:
        original = conn.execute("SELECT * FROM corporate_action_isins").fetchone()
        manifest = conn.execute("SELECT manifest FROM corporate_action_fetches WHERE id = %s", (fetch_id,)).fetchone()

    def upgrade(source, native):
        return {**extract(source, native), "headline": "Upgraded metadata", "extractor_version": "test-v2"}

    store.index_fetch(fetch_id, upgrade)
    with connect() as conn:
        action = conn.execute("SELECT headline, extractor_version FROM corporate_actions").fetchone()
        assert action == {"headline": "Upgraded metadata", "extractor_version": "test-v2"}
        assert conn.execute("SELECT * FROM corporate_action_isins").fetchone() == original
        assert conn.execute("SELECT manifest FROM corporate_action_fetches WHERE id = %s", (fetch_id,)).fetchone() == manifest
    assert store.pending_fetches() == []


def test_collector_end_to_end_new_bond_history_and_equity_exclusion(storage):
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    store, connect = storage
    other = "INE926R07050"
    scope = Scope({ISIN}, {})
    calls = []

    class Client:
        def get(self, request):
            calls.append(request)
            rows = [
                {"seq_id": isin + request.date_to.isoformat(),
                 "an_dt": request.date_to.strftime("%d-%b-%Y") + " 12:00:00",
                 "attchmntText": "ISIN - " + isin}
                for isin in (ISIN, other, "INE123A01001")
            ]
            return Response(200, json.dumps(rows).encode())

    collector = Collector(store, Client(), lambda: scope, sleep=lambda _: None)
    today = date(2026, 9, 10)
    first = collector.capture([SOURCES[0]], mode="backfill", date_from=date(2026, 9, 1),
                              date_to=date(2026, 9, 9), today=today)
    assert first["ok"], first
    assert store.load_targets(SOURCES[0])[ISIN]["state"] == "complete"
    calls_before = len(calls)
    same = collector.capture([SOURCES[0]], mode="backfill", date_from=date(2026, 9, 1),
                             date_to=date(2026, 9, 9), today=today)
    assert same["ok"], same
    assert len(calls) == calls_before
    scope.isins.add(other)
    result = collector.capture([SOURCES[0]], today=today, history_budget=1)
    assert result["ok"], result
    targets = store.load_targets(SOURCES[0])
    assert targets[other]["history_from"] == date(2026, 9, 1)
    assert targets[other]["state"] == targets[ISIN]["state"] == "complete"
    assert not targets[other]["pending"]
    assert not store.pending_fetches(SOURCES[0])
    assert not store.unfinished(SOURCES[0])
    with connect() as conn:
        rows = conn.execute("SELECT content FROM corporate_action_raw").fetchall()
        assert rows
        for row in rows:
            assert b"INE123A01001" not in gzip.decompress(row["content"])
        assert {r["isin"] for r in conn.execute("SELECT DISTINCT isin FROM corporate_action_isins")} == {ISIN, other}
