from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from backend.corporate_action_sources import SOURCES
from scripts.harvest_corporate_actions import parse_args


def test_recurring_capture_defaults_to_no_inline_history():
    args = parse_args(["capture"])
    assert args.history_budget == 0
    assert not args.dispatch_history


def test_history_command_does_not_require_or_reconfigure_dates():
    args = parse_args(["history", "--source", "nse", "--batch-requests", "2"])
    assert args.command == "history"
    assert args.batch_requests == 2
    assert not hasattr(args, "date_from")


@pytest.mark.parametrize("argv", [
    ["history", "--batch-requests", "0"],
    ["history", "--pause-seconds", "-1"],
    ["capture", "--dispatch-history", "--history-budget", "1"],
    ["capture", "--mode", "backfill", "--from", "2000-01-01", "--to", "2026-01-01", "--dispatch-history"],
])
def test_invalid_worker_controls_are_rejected(argv):
    with pytest.raises(SystemExit):
        parse_args(argv)


class DispatcherStore:
    def __init__(self, running=()):
        self.running = set(running)

    def lock(self, key):
        return nullcontext(key not in self.running)


class DispatcherCollector:
    def __init__(self, statuses, running=()):
        self.statuses = statuses
        self.store = DispatcherStore(running)

    def history_status(self, sources):
        return {s: self.statuses[s] for s in sources}


def test_dispatch_does_not_spawn_when_history_is_complete_or_blocked():
    from scripts.harvest_corporate_actions import dispatch_history

    collector = DispatcherCollector({s: {"has_work": False} for s in SOURCES})
    calls = []
    result = dispatch_history(collector, list(SOURCES), launch=lambda *a, **kw: calls.append((a, kw)))
    assert result["state"] == "idle"
    assert calls == []


def test_dispatch_starts_detached_worker_with_no_cron_pipe_inheritance():
    import subprocess

    from scripts.harvest_corporate_actions import dispatch_history

    collector = DispatcherCollector({s: {"has_work": True} for s in SOURCES})
    calls = []

    def launch(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(pid=1234)

    result = dispatch_history(collector, list(SOURCES), launch=launch)
    assert result["state"] == "started"
    assert result["pid"] == 1234
    argv, kwargs = calls[0]
    assert argv[-3:] == ["history", "--source", "all"]
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True
    assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL


def test_completed_eligible_targets_do_not_relaunch_history():
    from scripts.harvest_corporate_actions import dispatch_history

    collector = DispatcherCollector({SOURCES[0]: {"eligible_targets": 6617, "remaining_days": 0, "state": "complete"}})
    calls = []
    assert dispatch_history(collector, [SOURCES[0]], launch=lambda *a, **kw: calls.append(a))["state"] == "idle"
    assert not calls


def test_no_healthy_sources_do_not_query_or_spawn_history():
    from scripts.harvest_corporate_actions import dispatch_history

    assert dispatch_history(None, [])["state"] == "idle"


def test_dispatch_with_real_collector_obeys_pending_not_total_target_count():
    from test_corporate_actions import ISIN, START, TODAY, Feed, MemoryStore

    from backend.corporate_action_sources import Response
    from backend.corporate_actions import Collector, Scope, enroll
    from scripts.harvest_corporate_actions import dispatch_history

    store = MemoryStore()
    targets = enroll({}, {ISIN: {"codes": []}}, TODAY, START)
    store.save_targets(SOURCES[0], targets)
    collector = Collector(store, Feed(Response(200, b"[]")), lambda: Scope({ISIN}, {}), sleep=lambda _: None)
    calls = []

    def launch(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(pid=10)

    assert dispatch_history(collector, [SOURCES[0]], launch=launch)["state"] == "started"
    targets[ISIN].update(pending=[], state="complete")
    store.save_targets(SOURCES[0], targets)
    assert dispatch_history(collector, [SOURCES[0]], launch=launch)["state"] == "idle"
    assert len(calls) == 1


def test_dispatch_does_not_start_duplicate_worker_for_busy_source():
    from scripts.harvest_corporate_actions import dispatch_history

    source = SOURCES[0]
    collector = DispatcherCollector({source: {"has_work": True}}, running=["history_worker:" + source])
    calls = []
    result = dispatch_history(collector, [source], launch=lambda *a, **kw: calls.append(a))
    assert result["state"] == "already_running"
    assert calls == []
