from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import harvest_nsdl


def test_financial_terms_are_refreshed_instead_of_cached_forever():
    now = datetime.now(UTC)
    for endpoint in ("coupondetail", "instruments", "redemptions"):
        tier = harvest_nsdl.ENDPOINTS[endpoint][1]
        assert tier == "weekly"
        assert harvest_nsdl.due((200, "hash", now - timedelta(days=8)), tier, False)
        assert not harvest_nsdl.due((200, "hash", now - timedelta(hours=1)), tier, False)


@pytest.mark.parametrize("status", [401, 403, 404, 429])
def test_previously_failed_static_requests_are_not_permanent(status):
    assert harvest_nsdl.due((status, "hash", datetime.now(UTC)), "static", False)


@pytest.mark.parametrize("tier", ["static", "weekly", "monthly", "quarterly"])
def test_a_stored_per_record_5xx_is_retried_weekly_not_every_run(tier):
    # 14/15 Sept 2026: the 16 chronic 503s were re-hit (4 attempts each)
    # on every run because failures were never stored.
    now = datetime.now(UTC)
    assert not harvest_nsdl.due((503, "hash", now - timedelta(days=1)), tier, False)
    assert harvest_nsdl.due((503, "hash", now - timedelta(days=8)), tier, False)
    assert harvest_nsdl.due((503, "hash", now), tier, True)


def test_valid_static_data_and_explicit_force_keep_existing_semantics():
    previous = (200, "hash", datetime.now(UTC))
    assert not harvest_nsdl.due(previous, "static", False)
    assert harvest_nsdl.due(previous, "static", True)
    assert harvest_nsdl.due(None, "weekly", False)


def test_throttling_is_retried_not_stored_as_a_terminal_answer(monkeypatch):
    responses = [SimpleNamespace(status_code=429, text="throttled", headers={"Retry-After": "2"}),
                 SimpleNamespace(status_code=200, text='{"coupensVo": {}}', headers={})]
    get = Mock(side_effect=responses)
    pauses = []
    monkeypatch.setattr(harvest_nsdl, "session", lambda **kwargs: SimpleNamespace(get=get))
    monkeypatch.setattr(harvest_nsdl.time, "sleep", pauses.append)
    assert harvest_nsdl.fetch("https://example.test/coupon", "test") == (200, '{"coupensVo": {}}')
    assert get.call_count == 2 and pauses == [2]


@pytest.mark.parametrize("status,text", [(403, "forbidden"), (400, '{"message":"Bad request"}')])
def test_nonterminal_client_errors_do_not_poison_the_cache(monkeypatch, status, text):
    monkeypatch.setattr(harvest_nsdl, "session", lambda **kwargs: SimpleNamespace(
        get=lambda *args, **kwargs: SimpleNamespace(status_code=status, text=text, headers={})))
    monkeypatch.setattr(harvest_nsdl.time, "sleep", lambda _: None)
    assert harvest_nsdl.fetch("https://example.test/coupon", "test") == (None, None)


def test_documented_no_record_response_remains_a_valid_answer(monkeypatch):
    text = '{"message":"No Record Found"}'
    monkeypatch.setattr(harvest_nsdl, "session", lambda **kwargs: SimpleNamespace(
        get=lambda *args, **kwargs: SimpleNamespace(status_code=400, text=text, headers={})))
    assert harvest_nsdl.fetch("https://example.test/coupon", "test") == (400, text)


# --- outage fail-fast --------------------------------------------------------

def _stub_session(monkeypatch, responses, canary=False):
    """Feed fetch() a fixed sequence of responses; no sleeping, no network.
    The canary is stubbed too so it never eats from the sequence."""
    get = Mock(side_effect=responses)
    monkeypatch.setattr(harvest_nsdl, "session", lambda **kwargs: SimpleNamespace(get=get))
    monkeypatch.setattr(harvest_nsdl.time, "sleep", lambda _: None)
    monkeypatch.setattr(harvest_nsdl, "upstream_alive", lambda isin=None: canary)
    harvest_nsdl.reset_outage_tracking()
    return get


def _r(status, text="", headers=None):
    return SimpleNamespace(status_code=status, text=text, headers=headers or {})


def test_flat_upstream_failures_abort_the_run_instead_of_grinding(monkeypatch):
    # 13 Sept 2026: NSDL answered 503 to all 192 calls for four minutes.
    # Twelve consecutive upstream failures with no success in between and
    # a dead canary is a dead upstream - stop and mark the run failed.
    get = _stub_session(monkeypatch, [_r(503)] * 200, canary=False)
    with pytest.raises(harvest_nsdl.NsdlOutage) as exc:
        for _ in range(50):
            harvest_nsdl.fetch("https://example.test/x", "t")
    assert get.call_count == harvest_nsdl.OUTAGE_STREAK
    assert "12 consecutive" in str(exc.value) and "503" in str(exc.value)
    # Once decided, other workers raise at once without re-probing.
    with pytest.raises(harvest_nsdl.NsdlOutage):
        harvest_nsdl.fetch("https://example.test/y", "t")
    assert get.call_count == harvest_nsdl.OUTAGE_STREAK + 1


def test_a_live_canary_turns_a_flat_503_streak_into_broken_records(monkeypatch):
    # 15 Sept 2026: the only due work was the 16 chronic per-record 503s,
    # which also produce a flat streak. NSDL answered the canary, so the
    # run carries on and the records are returned for storage.
    get = _stub_session(monkeypatch, [_r(503, '{"status":503}')] * 200, canary=True)
    for _ in range(5):
        assert harvest_nsdl.fetch("https://example.test/x", "t") == (503, '{"status":503}')
    assert get.call_count == 5 * harvest_nsdl.MAX_ATTEMPTS
    assert harvest_nsdl._outage["canary_ok"] >= 1
    assert "canary ok" in (harvest_nsdl.failure_note(0, 5) or "")


def test_connection_errors_are_failures_not_broken_records(monkeypatch):
    _stub_session(monkeypatch, [ConnectionError("reset")] * 4, canary=True)
    assert harvest_nsdl.fetch("https://example.test/x", "t") == (None, None)


def test_a_late_success_after_5xx_is_the_answer(monkeypatch):
    _stub_session(monkeypatch, [_r(503), _r(502), _r(200, '{"a": 1}')])
    assert harvest_nsdl.fetch("https://example.test/x", "t") == (200, '{"a": 1}')


def test_a_single_success_resets_the_outage_streak(monkeypatch):
    seq = [_r(503)] * 11 + [_r(200, '{"ok": true}')] + [_r(503)] * 11
    _stub_session(monkeypatch, seq)
    for _ in range(3):  # 4 attempts each: 503x4 -> None; 503x4; 503x3+200 -> success
        harvest_nsdl.fetch("https://example.test/x", "t")
    for _ in range(2):  # 11 more 503s, below threshold
        harvest_nsdl.fetch("https://example.test/x", "t")
    # No raise: the 200 in the middle broke the streak.


def test_dead_isin_answers_do_not_count_toward_an_outage(monkeypatch):
    # A 404 is NSDL *answering* - that ISIN is gone. Twenty of them in a
    # row is a batch of dead ISINs, not an outage.
    _stub_session(monkeypatch, [_r(404, "not found")] * 40)
    for _ in range(20):
        assert harvest_nsdl.fetch("https://example.test/x", "t") == (None, None)


def test_status_counts_are_recorded_for_the_run_note(monkeypatch):
    _stub_session(monkeypatch, [_r(503), _r(503), _r(502), _r(200, '{"a": 1}')])
    assert harvest_nsdl.fetch("https://example.test/x", "t") == (200, '{"a": 1}')
    assert harvest_nsdl.status_counts() == {503: 2, 502: 1}
    assert harvest_nsdl.failure_note(48) == "48 fetch failures (503x2, 502x1)"
    assert harvest_nsdl.failure_note(15, 0, canary_ok=True) == (
        "15 fetch failures (503x2, 502x1); canary ok - per-record NSDL errors, not an outage")
    assert harvest_nsdl.failure_note(0, 16) == "16 broken records (503x2, 502x1)"
    assert harvest_nsdl.failure_note(0, 0) is None


# --- run verdict --------------------------------------------------------------

@pytest.mark.parametrize("fetched,unchanged,failed,alive,expected", [
    (2852, 36544, 16, None, True),   # normal day: 16 known-dead ISINs
    (0, 0, 48, None, False),         # 13 Sept: attempted work, got nothing
    (0, 0, 48, False, False),        # ... and the canary confirms NSDL is down
    (0, 0, 15, True, True),          # 15 Sept: only the broken records were due; canary answered
    (0, 0, 20, None, False),         # the latent gap: under the old floor of 25 this read as ok
    (0, 0, 0, None, True),           # nothing was due today - legitimately fine
    (5, 100, 30, None, False),       # 30 > max(25, 1% of 135)
    (5000, 40000, 300, None, True),  # 300 <= 1% of 45300
])
def test_run_verdict_needs_the_canary_to_call_a_zero_yield_run_ok(
        fetched, unchanged, failed, alive, expected):
    assert harvest_nsdl.run_ok(fetched, unchanged, failed, alive) is expected


def test_verdict_only_probes_the_canary_when_nothing_came_back():
    probe = Mock(return_value=True)
    ok, canary = harvest_nsdl.verdict(
        {"fetched": 2852, "unchanged": 36544, "failed": 16, "broken": 0}, probe)
    assert (ok, canary) == (True, None) and not probe.called
    ok, canary = harvest_nsdl.verdict(
        {"fetched": 0, "unchanged": 0, "failed": 15, "broken": 0}, probe)
    assert (ok, canary) == (True, True) and probe.call_count == 1
    # A day where the only due work was stored broken records is fine too.
    ok, canary = harvest_nsdl.verdict(
        {"fetched": 0, "unchanged": 0, "failed": 0, "broken": 16}, probe)
    assert (ok, canary) == (True, True)
    ok, canary = harvest_nsdl.verdict(
        {"fetched": 0, "unchanged": 0, "failed": 48, "broken": 0}, Mock(return_value=False))
    assert (ok, canary) == (False, False)


def test_an_outage_aborts_the_whole_run_and_is_named_in_the_note(monkeypatch):
    # Before this, NsdlOutage was swallowed by the per-ISIN handler in
    # main(): the pool ground on and the run note never said "aborted".
    finished = {}
    monkeypatch.setattr(harvest_nsdl.sys, "argv", ["harvest_nsdl.py", "--isin", "A",
                                                   "--isin", "B", "--workers", "1"])
    monkeypatch.setattr(harvest_nsdl, "load_state", dict)
    monkeypatch.setattr(harvest_nsdl, "start_nsdl_run", lambda *a: 99)
    monkeypatch.setattr(harvest_nsdl, "finish_nsdl_run",
                        lambda run_id, ok, notes=None: finished.update(run_id=run_id, ok=ok, notes=notes))
    monkeypatch.setattr(harvest_nsdl.db, "close_pool", lambda: None)

    def boom(*args):
        raise harvest_nsdl.NsdlOutage("12 consecutive upstream failures (http=503)")
    monkeypatch.setattr(harvest_nsdl, "harvest_isin", boom)
    with pytest.raises(harvest_nsdl.NsdlOutage):
        harvest_nsdl.main()
    assert finished["ok"] is False
    assert finished["notes"].startswith("aborted: NsdlOutage: 12 consecutive")


# --- conditional retry ----------------------------------------------------------

def test_if_failed_today_skips_when_the_morning_run_succeeded(monkeypatch, capsys):
    monkeypatch.setattr(harvest_nsdl, "latest_run_today", lambda: {"id": 65, "ok": True})
    assert harvest_nsdl.should_skip_retry() is True
    assert "skipping" in capsys.readouterr().out


@pytest.mark.parametrize("latest", [{"id": 64, "ok": False}, None])
def test_if_failed_today_proceeds_when_the_morning_run_failed_or_is_missing(monkeypatch, latest):
    monkeypatch.setattr(harvest_nsdl, "latest_run_today", lambda: latest)
    assert harvest_nsdl.should_skip_retry() is False
