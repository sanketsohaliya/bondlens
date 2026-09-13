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


@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
def test_previously_failed_static_requests_are_not_permanent(status):
    assert harvest_nsdl.due((status, "hash", datetime.now(UTC)), "static", False)


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

def _stub_session(monkeypatch, responses):
    """Feed fetch() a fixed sequence of responses; no sleeping, no network."""
    get = Mock(side_effect=responses)
    monkeypatch.setattr(harvest_nsdl, "session", lambda **kwargs: SimpleNamespace(get=get))
    monkeypatch.setattr(harvest_nsdl.time, "sleep", lambda _: None)
    harvest_nsdl.reset_outage_tracking()
    return get


def _r(status, text="", headers=None):
    return SimpleNamespace(status_code=status, text=text, headers=headers or {})


def test_flat_upstream_failures_abort_the_run_instead_of_grinding(monkeypatch):
    # 13 Sept 2026: NSDL answered 503 to all 192 calls for four minutes.
    # Twelve consecutive upstream failures with no success in between is
    # a dead upstream, not dead ISINs - stop and mark the run failed.
    get = _stub_session(monkeypatch, [_r(503)] * 200)
    with pytest.raises(harvest_nsdl.NsdlOutage) as exc:
        for _ in range(50):
            harvest_nsdl.fetch("https://example.test/x", "t")
    assert get.call_count == harvest_nsdl.OUTAGE_STREAK
    assert "12 consecutive" in str(exc.value) and "503" in str(exc.value)


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


# --- run verdict --------------------------------------------------------------

@pytest.mark.parametrize("fetched,unchanged,failed,expected", [
    (2852, 36544, 16, True),    # normal day: 16 known-dead ISINs
    (0, 0, 48, False),          # 13 Sept: attempted work, got nothing
    (0, 0, 20, False),          # the latent gap: under the old floor of 25 this read as ok
    (0, 0, 0, True),            # nothing was due today - legitimately fine
    (5, 100, 30, False),        # 30 > max(25, 1% of 135)
    (5000, 40000, 300, True),   # 300 <= 1% of 45300
])
def test_run_verdict_never_calls_a_zero_yield_run_ok(fetched, unchanged, failed, expected):
    assert harvest_nsdl.run_ok(fetched, unchanged, failed) is expected


# --- conditional retry ----------------------------------------------------------

def test_if_failed_today_skips_when_the_morning_run_succeeded(monkeypatch, capsys):
    monkeypatch.setattr(harvest_nsdl, "latest_run_today", lambda: {"id": 65, "ok": True})
    assert harvest_nsdl.should_skip_retry() is True
    assert "skipping" in capsys.readouterr().out


@pytest.mark.parametrize("latest", [{"id": 64, "ok": False}, None])
def test_if_failed_today_proceeds_when_the_morning_run_failed_or_is_missing(monkeypatch, latest):
    monkeypatch.setattr(harvest_nsdl, "latest_run_today", lambda: latest)
    assert harvest_nsdl.should_skip_retry() is False
