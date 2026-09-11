from datetime import date

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

    def lock(self, source):
        from contextlib import nullcontext
        return nullcontext(True)
