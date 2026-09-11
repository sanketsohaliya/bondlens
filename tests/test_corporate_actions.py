from contextlib import AbstractContextManager
from datetime import date
from typing import Any

import pytest

from backend.corporate_actions import enroll, history_work, subtract_window

TODAY = date(2026, 9, 10)
START = date(2020, 1, 1)
ISIN = "INE015X07042"
OTHER = "INE926R07050"


def test_new_bond_gets_history_without_restarting_completed_bond():
    existing = enroll({}, {ISIN: {"codes": []}}, TODAY, START)
    existing[ISIN].update(state="complete", pending=[])
    result = enroll(existing, {ISIN: {"codes": []}, OTHER: {"codes": []}}, TODAY, START)
    assert result[ISIN]["pending"] == []
    assert result[ISIN]["state"] == "complete"
    assert result[OTHER]["pending"] == [{"from": "2020-01-01", "to": "2026-09-09"}]
    assert result[OTHER]["state"] == "pending"


def test_completed_interval_splits_remaining_history_without_losing_holes():
    pending = [{"from": "2026-08-01", "to": "2026-08-31"}]
    assert subtract_window(pending, date(2026, 8, 10), date(2026, 8, 20)) == [
        {"from": "2026-08-01", "to": "2026-08-09"},
        {"from": "2026-08-21", "to": "2026-08-31"},
    ]


def test_multiple_new_bonds_share_newest_history_window():
    targets = enroll({}, {ISIN: {"codes": []}, OTHER: {"codes": []}}, TODAY, START)
    start, end, isins = next(history_work(targets, window_days=14))
    assert (start, end) == (date(2026, 8, 27), date(2026, 9, 9))
    assert isins == {ISIN, OTHER}


def test_unconfigured_archive_bounds_block_history_but_keep_target_active():
    target = enroll({}, {ISIN: {"codes": []}}, TODAY, None)[ISIN]
    assert target["active"] is True
    assert target["state"] == "blocked"
    assert target["blocked_reason"] == "history_start_unconfigured"
    assert list(history_work({ISIN: target})) == []


def test_missing_bse_mapping_does_not_gain_history_coverage():
    mapping = {"codes": [], "blocked_reason": "no_verified_scrip"}
    target = enroll({}, {ISIN: mapping}, TODAY, START)[ISIN]
    assert target["state"] == "blocked"
    assert target["pending"]


def test_relisted_bond_keeps_evidence_and_queues_only_missed_gap():
    existing = enroll({}, {ISIN: {"codes": []}}, date(2026, 9, 1), START)
    existing[ISIN].update(state="complete", pending=[])
    paused = enroll(existing, {}, date(2026, 9, 2), START)
    assert paused[ISIN]["state"] == "paused"
    resumed = enroll(paused, {ISIN: {"codes": []}}, TODAY, START)[ISIN]
    assert resumed["pending"] == [{"from": "2026-09-01", "to": "2026-09-09"}]
    assert resumed["generation"] == 2


def test_same_mapping_does_not_reset_completed_history_or_change_generation():
    existing = enroll({}, {ISIN: {"codes": ["977935"]}}, TODAY, START)
    existing[ISIN].update(state="complete", pending=[])
    result = enroll(existing, {ISIN: {"codes": ["977935"]}}, date(2026, 9, 11), START)[ISIN]
    assert result["pending"] == []
    assert result["generation"] == 1


def test_completed_history_is_requeued_for_a_new_source_mapping():
    existing = enroll({}, {ISIN: {"codes": ["977935"]}}, TODAY, START)
    existing[ISIN].update(state="complete", pending=[])
    result = enroll(existing, {ISIN: {"codes": ["977935", "977936"]}}, TODAY, START)[ISIN]
    assert result["generation"] == 2
    assert result["pending"] == [{"from": "2020-01-01", "to": "2026-09-09"}]


def test_recent_windows_differ_by_native_date_axis():
    from backend.corporate_actions import recent_window

    assert recent_window("nse_debt_announcements", TODAY) == (date(2026, 9, 4), TODAY)
    assert recent_window("bse_corporate_actions", TODAY) == (date(2026, 8, 11), date(2026, 12, 9))


def test_collector_archives_only_verified_rows_and_refreshes_each_run():
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    store = MemoryStore()
    response = Response(200, (
        b'[{"seq_id":"1","an_dt":"10-SEP-2026 12:00:00","attchmntText":"ISIN - INE015X07042"},'
        b'{"seq_id":"2","an_dt":"10-SEP-2026 12:00:00","attchmntText":"equity only"}]'
    ))
    client = Feed(response)
    collector = Collector(store, client, lambda: Scope({ISIN}, {}), sleep=lambda _: None)
    result = collector.capture([SOURCES[0]], today=TODAY, history_budget=0)
    assert result["ok"]
    assert len(store.rows) == 1
    assert "equity" not in str(store.rows)
    assert next(f for f in store.fetch_log if f["status"] == "success")["meta"]["exclusion_counts"] == {"unmatched_scope": 1}
    assert store.targets[SOURCES[0]][ISIN]["blocked_reason"] == "history_start_unconfigured"
    collector.capture([SOURCES[0]], today=TODAY, history_budget=0)
    assert len(client.requests) == 2


def test_history_successful_empty_window_is_completed_once():
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    store = MemoryStore()
    client = Feed(Response(200, b"[]"))
    collector = Collector(store, client, lambda: Scope({ISIN}, {}), sleep=lambda _: None)
    result = collector.capture([SOURCES[0]], mode="backfill", date_from=date(2026, 9, 1),
                               date_to=date(2026, 9, 9), today=TODAY, history_budget=2)
    assert result["ok"]
    assert store.targets[SOURCES[0]][ISIN]["state"] == "complete"
    collector.capture([SOURCES[0]], mode="backfill", date_from=date(2026, 9, 1),
                      date_to=date(2026, 9, 9), today=TODAY, history_budget=2)
    assert len(client.requests) == 1


def test_dry_run_does_not_fetch_enroll_or_open_run():
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    store = MemoryStore()
    client = Feed(Response(200, b"[]"))
    result = Collector(store, client, lambda: Scope({ISIN}, {})).capture(
        list(SOURCES), today=TODAY, dry_run=True,
    )
    assert result["dry_run"]
    assert not client.requests
    assert not store.targets and not store.runs and not store.fetch_log


def test_cli_requires_explicit_historical_bounds_and_rejects_invalid_budgets():
    import pytest

    from scripts.harvest_corporate_actions import parse_args

    with pytest.raises(SystemExit):
        parse_args(["capture", "--mode", "backfill"])
    with pytest.raises(SystemExit):
        parse_args(["capture", "--window-days", "0"])
    with pytest.raises(SystemExit):
        parse_args(["capture", "--mode", "recent", "--from", "2020-01-01"])
    args = parse_args(["capture", "--mode", "backfill", "--from", "2020-01-01", "--to", "2026-09-09"])
    assert args.date_from == START
    assert args.date_to == date(2026, 9, 9)


def test_capture_health_uses_recent_runs_not_latest_action_date():
    from datetime import UTC, datetime, timedelta

    from backend.corporate_actions import capture_health

    now = datetime(2026, 9, 10, 14, tzinfo=UTC)
    state = {"nse_debt_announcements": {
        "last_recent_success": now - timedelta(hours=20),
        "latest_recent": {"state": "failed"}, "pending_index": 2,
        "stuck_runs": 1, "unfinished_recent": 1,
    }}
    problems = capture_health(state, now)
    assert any("stale" in p for p in problems)
    assert any("index" in p for p in problems)
    assert any("stuck" in p for p in problems)


def test_source_failure_does_not_block_other_exchange():
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    class MixedFeed(Feed):
        def get(self, request):
            self.requests.append(request)
            return Response(403, b"blocked") if request.source == SOURCES[0] else Response(200, b"[]")

    store = MemoryStore()
    client = MixedFeed(None)
    result = Collector(store, client, lambda: Scope({ISIN}, {"977935": {ISIN}}), sleep=lambda _: None).capture(
        list(SOURCES), today=TODAY, history_budget=0,
    )
    assert not result["ok"]
    assert result["sources"][SOURCES[1]]["recent"]["ok"]
    assert sum(r.source == SOURCES[0] for r in client.requests) == 1
    assert "blocked" not in str(store.rows)


def test_small_bse_budget_advances_outstanding_windows():
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    store = MemoryStore()
    client = Feed(Response(200, b"[]"))
    collector = Collector(store, client, lambda: Scope({ISIN}, {"977935": {ISIN}}), sleep=lambda _: None)
    for _ in range(3):
        collector.capture([SOURCES[1]], today=TODAY, request_budget=1, history_budget=0)
    assert len({(r.date_from, r.date_to) for r in client.requests}) == 3
    assert store.targets[SOURCES[1]][ISIN]["recent_pending"]
    assert len(store.fetch_log) < 20


def test_scoped_history_does_not_configure_other_bonds():
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    store = MemoryStore()
    collector = Collector(store, Feed(Response(200, b"[]")), lambda: Scope({ISIN, OTHER}, {}), sleep=lambda _: None)
    collector.capture([SOURCES[0]], mode="backfill", date_from=date(2026, 9, 1),
                      date_to=date(2026, 9, 9), today=TODAY, only={ISIN})
    collector.capture([SOURCES[0]], today=TODAY, history_budget=0)
    assert store.targets[SOURCES[0]][OTHER]["history_from"] is None
    assert store.targets[SOURCES[0]][ISIN]["state"] == "complete"


def test_global_history_configuration_survives_empty_universe():
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    store = MemoryStore()
    scope = Scope(set(), {})
    collector = Collector(store, Feed(Response(200, b"[]")), lambda: scope, sleep=lambda _: None)
    collector.capture([SOURCES[0]], mode="backfill", date_from=START, date_to=date(2026, 9, 9), today=TODAY)
    scope.isins.add(ISIN)
    collector.capture([SOURCES[0]], today=TODAY, history_budget=0)
    assert store.targets[SOURCES[0]][ISIN]["history_from"] == START


def test_downtime_gap_survives_budget_exhaustion():
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    store = MemoryStore()
    collector = Collector(store, Feed(Response(200, b"[]")), lambda: Scope({ISIN}, {}), sleep=lambda _: None)
    collector.capture([SOURCES[0]], today=date(2026, 9, 1), history_budget=0)
    collector.capture([SOURCES[0]], today=date(2026, 10, 1), request_budget=1, history_budget=0)
    pending = store.targets[SOURCES[0]][ISIN]["recent_pending"]
    assert pending and min(w["from"] for w in pending) == "2026-09-01"
    assert store.unfinished(SOURCES[0])


def test_waiting_history_progress_is_not_starved_by_new_bonds():
    from datetime import UTC, datetime

    old = enroll({}, {ISIN: {"codes": []}}, date(2026, 8, 1), START)
    old[ISIN]["pending"] = [{"from": "2020-01-01", "to": "2020-12-31"}]
    old[ISIN]["last_progress_at"] = datetime(2026, 8, 1, tzinfo=UTC)
    new = enroll(old, {ISIN: {"codes": []}, OTHER: {"codes": []}}, TODAY, START)
    _, end, selected = next(history_work(new))
    assert end == date(2020, 12, 31)
    assert ISIN in selected


def test_index_failure_does_not_abandon_other_requests():
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    class FailingStore(MemoryStore):
        def index_fetch(self, fetch_id, extractor):
            if not any(f["index_status"] == "failed" for f in self.fetch_log):
                self.fetch_log[fetch_id - 1]["index_status"] = "failed"
                raise RuntimeError("temporary index failure")
            return super().index_fetch(fetch_id, extractor)

    store = FailingStore()
    client = Feed(Response(200, b"[]"))
    collector = Collector(store, client, lambda: Scope({ISIN}, {"977935": {ISIN}}), sleep=lambda _: None)
    result = collector.capture([SOURCES[1]], today=TODAY, history_budget=0)
    assert not result["ok"]
    assert len(client.requests) > 1
    assert len(store.pending_fetches(SOURCES[1])) == 1
    assert collector.replay([SOURCES[1]])["ok"]
    assert not store.pending_fetches(SOURCES[1])


@pytest.mark.parametrize("mode", ["backfill", "refetch"])
def test_manual_capture_preserves_recent_downtime_watermark(mode):
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    source = SOURCES[0]
    store = MemoryStore()
    collector = Collector(store, Feed(Response(200, b"[]")), lambda: Scope({ISIN}, {}), sleep=lambda _: None)
    collector.capture([source], today=date(2026, 9, 1), history_budget=0)
    collector.capture([source], mode=mode, date_from=date(2026, 8, 1), date_to=date(2026, 8, 2),
                      today=date(2026, 10, 1), only={ISIN})
    assert store.targets[source][ISIN]["last_active_date"] == date(2026, 9, 1)
    collector.capture([source], today=date(2026, 10, 1), request_budget=1, history_budget=0, window_days=7)
    pending = store.targets[source][ISIN]["recent_pending"]
    assert subtract_window(pending, date(2026, 9, 2), date(2026, 9, 24)) != pending
    assert any(w["from"] <= "2026-09-02" and w["to"] >= "2026-09-24" for w in pending)


def test_recent_watermark_and_missing_obligations_are_saved_together():
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    class InterruptedStore(MemoryStore):
        interrupted = False

        def save_targets(self, source, targets):
            super().save_targets(source, targets)
            if self.interrupted:
                self.interrupted = False
                raise RuntimeError("interrupted after target transaction")

    source = SOURCES[0]
    store = InterruptedStore()
    collector = Collector(store, Feed(Response(200, b"[]")), lambda: Scope({ISIN}, {}), sleep=lambda _: None)
    collector.capture([source], today=date(2026, 9, 1), history_budget=0)
    store.interrupted = True
    assert not collector.capture([source], today=date(2026, 10, 1), history_budget=0)["ok"]
    collector.capture([source], today=date(2026, 10, 1), request_budget=1, history_budget=0, window_days=7)
    assert any(w["from"] <= "2026-09-02" and w["to"] >= "2026-09-24"
               for w in store.targets[source][ISIN]["recent_pending"])


def test_first_recent_after_historical_enrollment_keeps_gap():
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    source = SOURCES[0]
    store = MemoryStore()
    collector = Collector(store, Feed(Response(200, b"[]")), lambda: Scope({ISIN}, {}), sleep=lambda _: None)
    collector.capture([source], mode="backfill", date_from=date(2026, 9, 1), date_to=date(2026, 9, 9), today=TODAY)
    assert store.targets[source][ISIN]["pending"] == []
    collector.capture([source], mode="backfill", date_from=date(2026, 8, 1), date_to=date(2026, 8, 2),
                      today=date(2026, 10, 1))
    collector.capture([source], today=date(2026, 10, 1), request_budget=1, history_budget=0, window_days=7)
    assert any(w["from"] <= "2026-09-10" and w["to"] >= "2026-09-24"
               for w in store.targets[source][ISIN]["recent_pending"])


@pytest.mark.parametrize("only", [None, {ISIN}])
def test_backfill_extends_completed_history_to_requested_right_bound(only):
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    source = SOURCES[0]
    store = MemoryStore()
    client = Feed(Response(200, b"[]"))
    collector = Collector(store, client, lambda: Scope({ISIN, OTHER}, {}), sleep=lambda _: None)
    collector.capture([source], mode="backfill", date_from=date(2026, 9, 1), date_to=date(2026, 9, 9), today=TODAY)
    result = collector.capture([source], mode="backfill", date_from=date(2026, 9, 10),
                               date_to=date(2026, 9, 30), today=date(2026, 10, 1), only=only)
    assert result["ok"]
    assert len(client.requests) == 3
    assert all(r.date_from >= date(2026, 9, 10) for r in client.requests[1:])
    assert store.targets[source][ISIN]["history_to"] == date(2026, 9, 30)
    assert store.targets[source][ISIN]["pending"] == []
    if only:
        assert store.targets[source][OTHER]["history_to"] == date(2026, 9, 9)
    collector.capture([source], mode="backfill", date_from=date(2026, 9, 1), date_to=date(2026, 9, 30),
                      today=date(2026, 10, 1), only=only)
    assert len(client.requests) == 3


def test_disjoint_right_extension_keeps_unrequested_gap_pending():
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    source = SOURCES[0]
    store = MemoryStore()
    client = Feed(Response(200, b"[]"))
    collector = Collector(store, client, lambda: Scope({ISIN}, {}), sleep=lambda _: None)
    collector.capture([source], mode="backfill", date_from=date(2026, 9, 1), date_to=date(2026, 9, 9), today=TODAY)
    collector.capture([source], mode="backfill", date_from=date(2026, 9, 20), date_to=date(2026, 9, 30),
                      today=date(2026, 10, 1))
    assert store.targets[source][ISIN]["pending"] == [{"from": "2026-09-10", "to": "2026-09-19"}]
    collector.capture([source], mode="backfill", date_from=date(2026, 9, 10), date_to=date(2026, 9, 19),
                      today=date(2026, 10, 1))
    assert store.targets[source][ISIN]["pending"] == []
    assert [(r.date_from, r.date_to) for r in client.requests] == [
        (date(2026, 9, 1), date(2026, 9, 9)),
        (date(2026, 9, 20), date(2026, 9, 30)),
        (date(2026, 9, 10), date(2026, 9, 19)),
    ]


def test_refetch_does_not_reuse_failed_recent_request_key():
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    class SharedKeyStore(MemoryStore):
        def queue_fetch(self, run_id, source, date_from, date_to, spec):
            for entry in reversed(self.fetch_log):
                if (entry["source"] == source and entry["spec"]["request_key"] == spec["request_key"]
                        and not entry.get("resolved_at")):
                    return entry["id"]
            return super().queue_fetch(run_id, source, date_from, date_to, spec)

    source = SOURCES[0]
    store = SharedKeyStore()
    client = Feed(Response(403, b"blocked"))
    collector = Collector(store, client, lambda: Scope({ISIN}, {}), sleep=lambda _: None)
    collector.capture([source], today=TODAY, history_budget=0)
    client.response = Response(200, b"[]")
    result = collector.capture([source], mode="refetch", date_from=date(2026, 9, 4), date_to=TODAY, today=TODAY)
    assert result["ok"]
    assert len(client.requests) == 2
    assert any(f["spec"]["kind"] == "refetch" and f["status"] == "empty" for f in store.fetch_log)


@pytest.mark.parametrize("mode", ["recent", "backfill", "refetch"])
@pytest.mark.parametrize("budget", [1, 2])
def test_split_children_resume_without_recapturing_parent_or_completed_sibling(mode, budget):
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    class SplitFeed(Feed):
        def get(self, request):
            self.requests.append(request)
            return Response(200, b'[]' if request.date_from == request.date_to else b'{"data": [], "total": 1}')

    source = SOURCES[0]
    store = MemoryStore()
    client = SplitFeed(None)
    collector = Collector(store, client, lambda: Scope({ISIN}, {}), sleep=lambda _: None)
    start = None if mode == "recent" else date(2026, 9, 9)
    end = None if mode == "recent" else TODAY
    kind = "history" if mode == "backfill" else mode
    for _ in range(20):
        collector.capture([source], mode=mode, today=TODAY, request_budget=budget, history_budget=0,
                          window_days=2, date_from=start, date_to=end)
        completed_days = {f["window_from"] for f in store.fetch_log
                          if f["window_from"] == f["window_to"] and f["index_status"] == "indexed"}
        if {date(2026, 9, 9), TODAY} <= completed_days:
            break
    bounds = [(r.date_from, r.date_to) for r in client.requests if r.date_from >= date(2026, 9, 9)]
    assert len(bounds) == len(set(bounds))
    assert (date(2026, 9, 9), date(2026, 9, 9)) in bounds
    assert (TODAY, TODAY) in bounds
    assert all(f.get("resolved_at") for f in store.fetch_log
               if f["status"] == "split" and f["window_from"] == date(2026, 9, 9))
    if mode != "backfill":
        collector.capture([source], mode=mode, today=TODAY, history_budget=0, window_days=2, date_from=start, date_to=end)
        assert not store.unfinished(source, kind)
        before = len(client.requests)
        collector.capture([source], mode=mode, today=TODAY, history_budget=0, window_days=2, date_from=start, date_to=end)
        assert not store.unfinished(source, kind)
        refreshed = [(r.date_from, r.date_to) for r in client.requests[before:]]
        assert (date(2026, 9, 9), date(2026, 9, 9)) in refreshed
        assert (TODAY, TODAY) in refreshed


def test_interrupted_split_queue_repairs_children_without_refetching_parent():
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    class InterruptedStore(MemoryStore):
        interrupted = True

        def queue_fetch(self, run_id, source, date_from, date_to, spec):
            if (self.interrupted and spec.get("parent_chain")
                    and any(f["spec"].get("parent_chain") for f in self.fetch_log)):
                self.interrupted = False
                raise RuntimeError("interrupted between child queues")
            return super().queue_fetch(run_id, source, date_from, date_to, spec)

    class SplitFeed(Feed):
        def get(self, request):
            self.requests.append(request)
            return Response(200, b'[]' if request.date_from == request.date_to else b'{"data": [], "total": 1}')

    source = SOURCES[0]
    store = InterruptedStore()
    client = SplitFeed(None)
    collector = Collector(store, client, lambda: Scope({ISIN}, {}), sleep=lambda _: None)
    assert not collector.capture([source], mode="refetch", date_from=date(2026, 9, 9), date_to=TODAY, today=TODAY)["ok"]
    assert len(client.requests) == 1
    assert collector.capture([source], mode="refetch", date_from=date(2026, 9, 9), date_to=TODAY, today=TODAY)["ok"]
    assert len(client.requests) == 3
    assert not store.unfinished(source, "refetch")


def test_split_parent_resolution_replays_before_coverage_is_marked():
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    class InterruptedStore(MemoryStore):
        interrupted = True

        def resolve_request(self, source, key):
            if self.interrupted and any(f["spec"]["request_key"] == key and f["status"] == "split"
                                        for f in self.fetch_log):
                self.interrupted = False
                raise RuntimeError("interrupted before parent resolution")
            super().resolve_request(source, key)

    class SplitFeed(Feed):
        def get(self, request):
            self.requests.append(request)
            return Response(200, b'[]' if request.date_from == request.date_to else b'{"data": [], "total": 1}')

    source = SOURCES[0]
    store = InterruptedStore()
    client = SplitFeed(None)
    collector = Collector(store, client, lambda: Scope({ISIN}, {}), sleep=lambda _: None)
    kwargs = {"mode": "refetch", "date_from": date(2026, 9, 7), "date_to": TODAY, "today": TODAY}
    assert not collector.capture([source], **kwargs)["ok"]
    assert store.coverage_fetches(source)
    assert collector.replay([source])["ok"]
    assert not store.coverage_fetches(source)
    assert collector.capture([source], **kwargs)["ok"]
    assert not store.unfinished(source, "refetch")
    assert all(f.get("resolved_at") for f in store.fetch_log if f["status"] == "split")
    bounds = [(r.date_from, r.date_to) for r in client.requests]
    assert len(bounds) == len(set(bounds))


def test_scope_contraction_does_not_send_stale_duplicate_root():
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    source = SOURCES[0]
    store = MemoryStore()
    client = Feed(Response(403, b"blocked"))
    scope = Scope({ISIN, OTHER}, {})
    collector = Collector(store, client, lambda: scope, sleep=lambda _: None)
    collector.capture([source], today=TODAY, history_budget=0)
    scope.isins = {ISIN}
    client.response = Response(200, b"[]")
    result = collector.capture([source], today=TODAY, history_budget=0)
    assert result["ok"]
    assert len(client.requests) == 2
    assert not store.unfinished(source)


def history_collector(start: date | None = date(2026, 8, 1), store: Any = None,
                      client: Any = None, sources: list[str] | None = None):
    from backend.corporate_action_sources import SOURCES, Response
    from backend.corporate_actions import Collector, Scope

    store = store or MemoryStore()
    client = client or Feed(Response(200, b"[]"))
    scope = Scope({ISIN}, {"977935": {ISIN}})
    collector = Collector(store, client, lambda: scope, sleep=lambda _: None)
    for source in sources or [SOURCES[0]]:
        store.targets[source] = enroll({}, scope.mappings(source), TODAY, start)
    return collector, store, client, scope


def test_default_recent_leaves_history_network_for_worker():
    from backend.corporate_action_sources import SOURCES

    collector, store, client, _ = history_collector()
    result = collector.capture([SOURCES[0]], today=TODAY)
    assert result["ok"]
    assert set(result["sources"][SOURCES[0]]) == {"recent"}
    assert [(r.date_from, r.date_to) for r in client.requests] == [(date(2026, 9, 4), TODAY)]
    assert store.targets[SOURCES[0]][ISIN]["pending"]


def test_history_status_is_read_only_and_reports_excluded_targets():
    from copy import deepcopy

    from backend.corporate_action_sources import SOURCES

    collector, store, client, _ = history_collector(start=date(2026, 9, 1))
    source = SOURCES[0]
    target = store.targets[source][ISIN]
    target["pending"] = [{"from": "2026-09-01", "to": "2026-09-03"},
                         {"from": "2026-09-07", "to": "2026-09-09"}]
    store.targets[source][OTHER] = {**deepcopy(target), "blocked_reason": "no_verified_scrip", "state": "blocked"}
    store.targets[source]["paused"] = {**deepcopy(target), "active": False, "state": "paused"}
    before = deepcopy(store.__dict__)
    status = collector.history_status([source])[source]
    assert status["eligible_targets"] == 1
    assert status["remaining_days"] == 6
    assert status["pending_index"] == status["pending_requests"] == 0
    assert status["blocked_targets"] == 1
    assert status["blocked_reasons"] == {"no_verified_scrip": 1}
    assert status["paused_targets"] == 1
    assert store.__dict__ == before
    assert not client.requests


def test_history_drain_completes_all_batches_once_without_resetting_goals():
    from copy import deepcopy

    from backend.corporate_action_sources import SOURCES

    collector, store, client, _ = history_collector()
    source = SOURCES[0]
    before = deepcopy(store.targets[source][ISIN])
    events = []
    result = collector.drain_history([source], batch_requests=1, window_days=7,
                                     pause_seconds=0, today=TODAY, on_progress=events.append)
    assert result["ok"] and result["state"] == "complete"
    assert len(client.requests) == 6
    assert len(events) == 6
    assert all(event["source"] == source and event["requests"] <= 1 for event in events)
    target = store.targets[source][ISIN]
    assert target["pending"] == []
    for key in ("history_from", "history_to", "generation", "last_active_date"):
        assert target[key] == before[key]
    assert all(run["mode"] == "backfill" and not run["spec"]["configure_history"] for run in store.runs)
    count = len(store.runs)
    assert collector.drain_history([source], today=TODAY)["state"] == "complete"
    assert len(client.requests) == 6
    assert len(store.runs) == count


def test_history_worker_new_enrollment_only_captures_missing_bond_history():
    from backend.corporate_action_sources import SOURCES

    collector, store, client, scope = history_collector(start=date(2026, 9, 1))
    source = SOURCES[0]
    assert collector.drain_history([source], today=TODAY)["ok"]
    scope.isins.add(OTHER)
    store.targets[source] = enroll(store.targets[source], scope.mappings(source), TODAY, date(2026, 9, 1))
    assert collector.drain_history([source], today=TODAY)["ok"]
    assert len(client.requests) == 2
    completed = [f for f in store.fetch_log if f["status"] == "empty"]
    assert set(completed[-1]["spec"]["scope"]) == {OTHER}
    assert store.targets[source][ISIN]["generation"] == 1


def test_history_worker_excludes_paused_and_blocked_without_claiming_complete():
    from backend.corporate_action_sources import SOURCES

    collector, store, client, scope = history_collector(start=date(2026, 9, 1))
    source = SOURCES[0]
    scope.isins.add(OTHER)
    store.targets[source] = enroll(store.targets[source], scope.mappings(source), TODAY, date(2026, 9, 1))
    store.targets[source][ISIN].update(active=False, state="paused")
    store.targets[source][OTHER].update(blocked_reason="history_start_unconfigured", state="blocked", history_from=None)
    result = collector.drain_history([source], today=TODAY)
    assert not result["ok"] and result["state"] == "blocked"
    assert result["sources"][source]["paused_targets"] == 1
    assert result["sources"][source]["blocked_targets"] == 1
    assert not client.requests and not store.runs


def test_history_worker_preserves_excluded_goals_while_draining_eligible_target():
    from copy import deepcopy

    from backend.corporate_action_sources import SOURCES

    collector, store, client, scope = history_collector(start=date(2026, 9, 9))
    source = SOURCES[0]
    scope.isins.update({OTHER, "blocked"})
    store.targets[source] = enroll(store.targets[source], scope.mappings(source), TODAY, date(2026, 9, 9))
    store.targets[source][OTHER].update(active=False, state="paused")
    store.targets[source]["blocked"].update(blocked_reason="no_verified_scrip", state="blocked")
    excluded = {i: deepcopy(store.targets[source][i]) for i in (OTHER, "blocked")}
    result = collector.drain_history([source], today=TODAY)
    assert result["state"] == "blocked"
    assert result["sources"][source]["remaining_days"] == 0
    assert store.targets[source][ISIN]["pending"] == []
    assert all(store.targets[source][i] == target for i, target in excluded.items())
    assert len(client.requests) == 1
    assert all(set(f["spec"]["scope"]) == {ISIN} for f in store.fetch_log)


def test_history_worker_unconfigured_source_is_not_complete():
    from backend.corporate_action_sources import SOURCES

    collector, store, client, _ = history_collector(start=None)
    assert collector.drain_history([SOURCES[0]], today=TODAY)["state"] == "blocked"
    store.targets.clear()
    assert collector.drain_history([SOURCES[0]], today=TODAY)["state"] == "blocked"
    assert not client.requests


def test_history_worker_locks_are_per_source_and_released_between_batches():
    from contextlib import contextmanager

    from backend.corporate_action_sources import SOURCES

    class LockStore(MemoryStore):
        held = set()

        @contextmanager
        def lock(self, source):
            if source == "history_worker:" + SOURCES[0]:
                yield False
                return
            assert source not in self.held
            self.held.add(source)
            try:
                yield True
            finally:
                self.held.remove(source)

    collector, store, client, _ = history_collector(store=LockStore(), sources=list(SOURCES))
    pauses = []

    def sleep(seconds):
        if seconds == 2:
            assert SOURCES[1] not in store.held
            assert "history_worker:" + SOURCES[1] in store.held
            pauses.append(seconds)

    collector.sleep = sleep
    result = collector.drain_history(list(SOURCES), batch_requests=1, pause_seconds=2, today=TODAY)
    assert result["state"] == "already_running"
    assert result["sources"][SOURCES[0]]["state"] == "already_running"
    assert result["sources"][SOURCES[1]]["state"] == "complete"
    assert client.requests and all(r.source == SOURCES[1] for r in client.requests)
    assert pauses and not store.held


def test_duplicate_history_worker_skips_source_without_reading_or_running_it():
    from contextlib import nullcontext

    from backend.corporate_action_sources import SOURCES

    class OwnedStore(MemoryStore):
        def lock(self, source):
            assert source == "history_worker:" + SOURCES[0]
            return nullcontext(False)

        def load_targets(self, source):
            raise AssertionError("another worker owns this source")

    collector, _, client, _ = history_collector(store=OwnedStore())
    result = collector.drain_history([SOURCES[0]], today=TODAY)
    assert result["state"] == "already_running" and result["ok"]
    assert not client.requests


def test_history_worker_continues_healthy_source_after_fatal_failure():
    from backend.corporate_action_sources import SOURCES, Response

    class MixedFeed(Feed):
        def get(self, request):
            self.requests.append(request)
            return Response(403, b"private response") if request.source == SOURCES[0] else Response(200, b"[]")

    collector, store, client, _ = history_collector(client=MixedFeed(None), sources=list(SOURCES))
    events = []
    result = collector.drain_history(list(SOURCES), batch_requests=1, today=TODAY, on_progress=events.append)
    assert not result["ok"] and result["state"] == "blocked"
    assert result["sources"][SOURCES[0]]["state"] == "blocked"
    assert result["sources"][SOURCES[0]]["errors"]
    assert result["sources"][SOURCES[1]]["state"] == "complete"
    assert sum(r.source == SOURCES[0] for r in client.requests) == 1
    assert not store.targets[SOURCES[1]][ISIN]["pending"]
    assert "private response" not in str(events)


def test_history_worker_split_queue_growth_counts_as_progress():
    from backend.corporate_action_sources import SOURCES, Response

    class SplitFeed(Feed):
        def get(self, request):
            self.requests.append(request)
            return Response(200, b"[]" if request.date_from == request.date_to else b'{"data": [], "total": 1}')

    collector, store, client, _ = history_collector(start=date(2026, 9, 1), client=SplitFeed(None))
    result = collector.drain_history([SOURCES[0]], batch_requests=1, today=TODAY)
    assert result["state"] == "complete"
    assert len(client.requests) == 17
    assert len({(r.date_from, r.date_to) for r in client.requests}) == 17
    assert not store.unfinished(SOURCES[0], "history")


@pytest.mark.parametrize("body", [b"not json", b'{"data": [], "total": 1}'])
def test_history_worker_invalid_or_unsplittable_response_stops_bounded(body):
    from backend.corporate_action_sources import SOURCES, Response

    collector, store, client, _ = history_collector(start=date(2026, 9, 9), client=Feed(Response(200, body)))
    result = collector.drain_history([SOURCES[0]], batch_requests=1, today=TODAY)
    assert result["state"] == "blocked" and not result["ok"]
    assert len(client.requests) == 1
    assert result["sources"][SOURCES[0]]["remaining_days"] == 1
    assert result["sources"][SOURCES[0]]["pending_requests"]


def test_history_worker_stable_no_progress_stops_after_three_cycles(monkeypatch):
    from datetime import UTC, datetime

    from backend.corporate_action_sources import SOURCES

    collector, store, client, _ = history_collector()
    calls = []

    def stalled(source, *args):
        calls.append(source)
        store.targets[source][ISIN]["last_progress_at"] = datetime.now(UTC)
        return {"ok": True, "requests": 0, "errors": []}

    monkeypatch.setattr(collector, "_phase", stalled)
    result = collector.drain_history([SOURCES[0]], today=TODAY)
    assert result["state"] == "blocked"
    assert result["sources"][SOURCES[0]]["errors"] == ["no_progress"]
    assert len(calls) == 3 and not client.requests


def test_history_worker_recovers_retained_index_without_network():
    from backend.corporate_action_sources import SOURCES
    from backend.corporate_actions import Collector

    class FailingStore(MemoryStore):
        failing = True

        def index_fetch(self, fetch_id, extractor):
            if self.failing:
                self.fetch_log[fetch_id - 1]["index_status"] = "failed"
                raise RuntimeError("index unavailable")
            return super().index_fetch(fetch_id, extractor)

    collector, store, client, _ = history_collector(start=date(2026, 9, 9), store=FailingStore())
    source = SOURCES[0]
    result = collector.drain_history([source], batch_requests=1, today=TODAY)
    assert result["state"] == "blocked"
    assert result["sources"][source]["pending_index"] == 1
    assert len(client.requests) == 1
    store.targets[source][ISIN]["pending"] = []
    assert Collector.history_status(collector, [source])[source]["pending_index"] == 1
    store.failing = False
    assert collector.drain_history([source], today=TODAY)["state"] == "complete"
    assert len(client.requests) == 1


def test_history_worker_repairs_unapplied_coverage_and_unresolved_receipts():
    from backend.corporate_action_sources import SOURCES, Request

    collector, store, client, _ = history_collector(start=date(2026, 9, 9))
    source = SOURCES[0]
    assert collector.drain_history([source], today=TODAY)["ok"]
    completed = next(f for f in store.fetch_log if f["status"] == "empty")
    completed.pop("coverage_applied_at")
    store.targets[source][ISIN]["pending"] = []
    assert collector.history_status([source])[source]["pending_coverage"] == 1
    assert collector.drain_history([source], today=TODAY)["state"] == "complete"
    assert len(client.requests) == 1
    request = Request(source, date(2026, 9, 8), date(2026, 9, 8))
    spec = collector._request_spec(request, collector._scope_tokens(store.targets[source], {ISIN}), "history")
    collector._queue(len(store.runs), request, spec)
    assert collector.history_status([source])[source]["pending_requests"] == 1
    assert collector.drain_history([source], today=TODAY)["state"] == "complete"
    assert len(client.requests) == 2
    assert not store.unfinished(source, "history")


def test_history_worker_recovers_many_equivalent_retained_receipts_without_stalling():
    from backend.corporate_action_sources import SOURCES, Request

    collector, store, client, _ = history_collector(start=date(2026, 9, 9))
    source = SOURCES[0]
    store.targets[source][ISIN].update(pending=[], state="complete")
    run_id = store.start_run(source, "backfill", {})
    request = Request(source, date(2026, 9, 9), date(2026, 9, 9))
    spec = collector._request_spec(request, collector._scope_tokens(store.targets[source], {ISIN}), "history")
    for _ in range(80):
        fetch_id = store.start_fetch(run_id, source, request.date_from, request.date_to, spec)
        store.save_response(fetch_id, {"fetch_status": "empty"}, [])
    result = collector.drain_history([source], today=TODAY)
    assert result["state"] == "complete"
    assert result["sources"][source]["batches"] == 4
    assert not store.pending_fetches(source) and not client.requests


def test_history_worker_yields_fair_rounds_and_retries_on_later_invocation():
    from backend.corporate_action_sources import SOURCES, Response

    collector, store, client, _ = history_collector(client=Feed(Response(403, b"blocked")), sources=list(SOURCES))
    assert collector.drain_history(list(SOURCES), batch_requests=1, today=TODAY)["state"] == "blocked"
    assert collector.stopped == set(SOURCES)
    client.requests.clear()
    client.response = Response(200, b"[]")
    assert collector.drain_history(list(SOURCES), batch_requests=1, today=TODAY)["state"] == "complete"
    assert [r.source for r in client.requests] == list(SOURCES) * 3
    assert not collector.stopped


def test_explicit_positive_recent_history_budget_preserves_legacy_behavior():
    from backend.corporate_action_sources import SOURCES

    collector, _, client, _ = history_collector()
    result = collector.capture([SOURCES[0]], today=TODAY, history_budget=1)
    assert set(result["sources"][SOURCES[0]]) == {"recent", "queued_history"}
    assert len(client.requests) == 2
    assert client.requests[-1].date_to < date(2026, 9, 4)


def test_history_worker_pending_recent_requests_are_never_sent():
    from backend.corporate_action_sources import SOURCES, Response

    collector, store, client, _ = history_collector()
    source = SOURCES[0]
    client.response = Response(403, b"blocked")
    assert not collector.capture([source], today=TODAY)["ok"]
    assert store.unfinished(source, "recent")
    client.response = Response(200, b"[]")
    client.requests.clear()
    assert collector.drain_history([source], today=TODAY)["state"] == "complete"
    assert all(r.date_to < TODAY for r in client.requests)
    assert store.unfinished(source, "recent")


def test_history_worker_source_lock_contention_retries_bounded():
    from contextlib import contextmanager

    from backend.corporate_action_sources import SOURCES

    class BusyStore(MemoryStore):
        attempts = 0
        busy = 2
        entered = False

        @contextmanager
        def lock(self, source):
            if source.startswith("history_worker:"):
                yield True
                return
            self.attempts += 1
            self.entered = True
            try:
                yield self.attempts > self.busy
            finally:
                self.entered = False

    collector, store, client, _ = history_collector(start=date(2026, 9, 9), store=BusyStore())
    waits = []

    def sleep(seconds):
        if seconds == 2:
            assert not store.entered
            waits.append(seconds)

    collector.sleep = sleep
    assert collector.drain_history([SOURCES[0]], today=TODAY)["state"] == "complete"
    assert store.attempts == 3 and len(waits) == 2
    store.attempts = 0
    store.busy = 100
    store.targets[SOURCES[0]][ISIN]["pending"] = [{"from": "2026-09-08", "to": "2026-09-08"}]
    result = collector.drain_history([SOURCES[0]], today=TODAY, source_lock_attempts=3)
    assert result["state"] == "blocked"
    assert result["sources"][SOURCES[0]]["errors"] == ["source_locked"]
    assert store.attempts == 3 and len(client.requests) == 1


def test_history_worker_waits_through_a_normal_recent_capture_batch():
    from contextlib import nullcontext

    from backend.corporate_action_sources import SOURCES

    class BusyStore(MemoryStore):
        attempts = 0

        def lock(self, source):
            if source.startswith("history_worker:"):
                return nullcontext(True)
            self.attempts += 1
            return nullcontext(self.attempts > 4)

    collector, store, client, _ = history_collector(start=date(2026, 9, 9), store=BusyStore())
    result = collector.drain_history([SOURCES[0]], today=TODAY)
    assert result["state"] == "complete"
    assert store.attempts == 5 and len(client.requests) == 1


def test_history_worker_pauses_targets_that_leave_the_current_universe():
    from backend.corporate_action_sources import SOURCES

    collector, store, client, scope = history_collector(start=date(2026, 9, 1))
    source = SOURCES[0]
    scope.isins.add(OTHER)
    store.targets[source] = enroll(store.targets[source], scope.mappings(source), TODAY, date(2026, 9, 1))
    scope.isins.remove(OTHER)
    result = collector.drain_history([source], today=TODAY)
    assert store.targets[source][OTHER]["state"] == "paused"
    assert store.targets[source][ISIN]["state"] == "complete"
    assert "no_progress" not in result["errors"].get(source, [])
    assert client.requests


def test_completed_source_releases_worker_lock_while_other_source_continues():
    from contextlib import contextmanager

    from backend.corporate_action_sources import SOURCES

    class TrackedStore(MemoryStore):
        held = set()

        @contextmanager
        def lock(self, source):
            self.held.add(source)
            try:
                yield True
            finally:
                self.held.remove(source)

    collector, store, _, _ = history_collector(start=date(2026, 9, 1), store=TrackedStore(), sources=list(SOURCES))
    store.targets[SOURCES[0]][ISIN]["pending"] = [{"from": "2026-09-09", "to": "2026-09-09"}]
    observations = []

    def progress(event):
        if event["source"] == SOURCES[1] and event["state"] == "pending":
            observations.append(True)
            assert "history_worker:" + SOURCES[0] not in store.held

    result = collector.drain_history(list(SOURCES), batch_requests=1, window_days=1, today=TODAY, on_progress=progress)
    assert result["state"] == "complete" and observations
    assert not store.held


def test_recent_capture_waits_for_source_lock_without_holding_it(monkeypatch):
    from contextlib import contextmanager

    from backend import corporate_actions
    from backend.corporate_action_sources import SOURCES

    class BusyStore(MemoryStore):
        attempts = 0
        entered = False
        busy = 2

        @contextmanager
        def lock(self, source):
            self.attempts += 1
            self.entered = True
            try:
                yield self.attempts > self.busy
            finally:
                self.entered = False

    collector, store, client, _ = history_collector(store=BusyStore())
    clock = [0]
    monkeypatch.setattr(corporate_actions, "monotonic", lambda: clock[0])

    def sleep(seconds):
        if seconds == 1:
            assert not store.entered
            clock[0] += seconds

    collector.sleep = sleep
    assert collector.capture([SOURCES[0]], today=TODAY, lock_wait_seconds=5)["ok"]
    assert store.attempts == 3 and len(client.requests) == 1
    store.attempts = 0
    store.busy = 100
    result = collector.capture([SOURCES[0]], today=TODAY, lock_wait_seconds=2)
    assert not result["ok"]
    assert result["sources"][SOURCES[0]]["recent"]["errors"] == ["source_locked"]
    assert store.attempts == 3 and len(client.requests) == 1


class Feed:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def get(self, request):
        self.requests.append(request)
        return self.response


class MemoryStore:
    def __init__(self):
        self.targets = {}
        self.runs = []
        self.fetch_log = []
        self.rows = []

    def load_targets(self, source):
        from copy import deepcopy
        return deepcopy(self.targets.get(source, {}))

    def save_target(self, source, isin, target):
        from copy import deepcopy
        self.targets.setdefault(source, {})[isin] = deepcopy(target)

    def start_run(self, source, mode, spec):
        self.runs.append({"source": source, "mode": mode, "spec": spec})
        return len(self.runs)

    def finish_run(self, run_id, ok, summary):
        self.runs[run_id - 1].update(ok=ok, summary=summary)

    def start_fetch(self, run_id, source, date_from, date_to, spec):
        self.fetch_log.append({
            "id": len(self.fetch_log) + 1, "run_id": run_id, "source": source,
            "mode": self.runs[run_id - 1]["mode"], "window_from": date_from,
            "window_to": date_to, "spec": spec, "status": "running", "index_status": "pending",
        })
        return len(self.fetch_log)

    def save_response(self, fetch_id, meta, records):
        entry = self.fetch_log[fetch_id - 1]
        entry.update(meta=meta, records=records, status=meta["fetch_status"],
                     index_status="pending" if meta["fetch_status"] in {"success", "empty"} else "not_applicable")
        self.rows.extend(records)

    def index_fetch(self, fetch_id, extractor):
        import json
        entry = self.fetch_log[fetch_id - 1]
        for record in entry.get("records", []):
            extractor(entry["source"], json.loads(record["text"]))
        entry["index_status"] = "indexed"
        return len(entry.get("records", []))

    def fetches(self, source):
        return [dict(f) for f in self.fetch_log if f["source"] == source]

    def pending_fetches(self, source=None, run_id=None, reindex=False):
        return [f for f in self.fetch_log if f["status"] in {"success", "empty"}
                and (reindex or f["index_status"] != "indexed") and (source is None or source == f["source"])
                and (run_id is None or run_id == f["run_id"])]

    def history_start(self, source):
        for run in reversed(self.runs):
            if run["source"] == source and run["spec"].get("configure_history"):
                return date.fromisoformat(run["spec"]["history_start"])
        return None

    def queue_fetch(self, run_id, source, date_from, date_to, spec):
        for f in reversed(self.unfinished(source, spec["kind"])):
            if f["spec"]["request_key"] == spec["request_key"]:
                return f["id"]
        fetch_id = self.start_fetch(run_id, source, date_from, date_to, spec)
        self.fetch_log[-1]["status"] = "pending"
        return fetch_id

    def save_targets(self, source, targets):
        for isin, target in targets.items():
            self.save_target(source, isin, target)

    def coverage_fetches(self, source):
        return [f for f in self.fetch_log if f["source"] == source and f["index_status"] == "indexed"
                and not f.get("coverage_applied_at")]

    def mark_coverage(self, fetch_id):
        self.fetch_log[fetch_id - 1]["coverage_applied_at"] = True

    def completed(self, source, key):
        for f in reversed(self.fetch_log):
            if f["source"] == source and f["spec"]["request_key"] == key:
                return f["index_status"] == "indexed" or f["status"] == "split" and f.get("resolved_at")
        return False

    def resolve_request(self, source, key):
        for f in self.fetch_log:
            if f["source"] == source and f["spec"]["request_key"] == key:
                f["resolved_at"] = True

    def unfinished(self, source, kind="recent"):
        latest = {f["spec"]["request_key"]: f for f in self.fetch_log
                  if f["source"] == source and f["spec"]["kind"] == kind}
        return sorted([f for f in latest.values() if not f.get("resolved_at")
                       and f["status"] not in {"success", "empty"}
                       and not self.completed(source, f["spec"]["request_key"])], key=lambda f: f["id"])

    def lock(self, source) -> AbstractContextManager[bool]:
        from contextlib import nullcontext
        return nullcontext(True)
