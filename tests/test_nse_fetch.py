"""NSE DEBT.csv fetcher: retry behavior (no network — requests is monkeypatched).

Regression for the 2026-07-20 universe run failure: a single early-morning
ReadTimeout from nsearchives.nseindia.com killed the whole run because the
NSE fetch, unlike the BSE one, had no retries.
"""
import pytest
import requests

from backend import nse


class _Resp:
    text = "SYMBOL,SERIES\n"

    @staticmethod
    def raise_for_status():
        pass


def test_retries_after_transient_timeout(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise requests.ReadTimeout("read timed out")
        return _Resp()

    monkeypatch.setattr(nse.requests, "get", fake_get)
    monkeypatch.setattr(nse.time, "sleep", lambda s: None)
    assert nse.fetch_debt_csv(retries=2) == "SYMBOL,SERIES\n"
    assert len(calls) == 2


def test_raises_after_exhausting_retries(monkeypatch):
    def fake_get(url, **kwargs):
        raise requests.ReadTimeout("read timed out")

    monkeypatch.setattr(nse.requests, "get", fake_get)
    monkeypatch.setattr(nse.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        nse.fetch_debt_csv(retries=2)
