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
