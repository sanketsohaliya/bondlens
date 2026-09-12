"""YTM @ Ask computation tests (no network, no DB)."""
from datetime import UTC, date, datetime, timedelta

import pytest

from backend import ask_ytm
from backend.analytics.schedule import Flow, Schedule


def _quote(ask_price, ask_qty) -> dict:
    return {"depth": {"sell": [{"price": ask_price, "quantity": ask_qty,
                                "orders": 1}]}}


@pytest.fixture
def env(monkeypatch):
    """Two bonds: one listed both venues, one BSE-only; fake quotes,
    schedules and yields (the yield engine has its own test suite)."""
    ask_ytm._cache.clear()
    monkeypatch.setattr(ask_ytm.queries, "bonds_symbols", lambda isins: {
        i: m for i, m in {
            "INE000TEST01": {"nse_symbol": "9TB27", "nse_series": "N1",
                             "bse_scrip_id": "9TB27B"},
            "INE000TEST02": {"nse_symbol": None, "nse_series": None,
                             "bse_scrip_id": "8XY29B"},
            "INE000TEST03": {"nse_symbol": "7QZ26", "nse_series": None,
                             "bse_scrip_id": None},
        }.items() if i in isins})
    monkeypatch.setattr(ask_ytm.kite, "resolve_nse",
                        lambda sym, series: (f"{sym}-{series}" if series else sym, 1)
                        if sym else None)
    monkeypatch.setattr(ask_ytm.kite, "resolve_bse",
                        lambda scrip: (scrip, 2) if scrip else None)

    calls = {"quotes": []}

    def fake_quotes(user_id, instruments):
        calls["quotes"].append(list(instruments))
        return {
            "NSE:9TB27-N1": _quote(1010.0, 25),
            "BSE:9TB27B": _quote(1005.0, 10),   # cheaper -> best ask
            # 8XY29B: no resting ask
            "BSE:8XY29B": {"depth": {"sell": []}},
            "NSE:7QZ26": _quote(990.0, 5),
        }

    monkeypatch.setattr(ask_ytm.kite_connect, "fetch_quotes", fake_quotes)
    monkeypatch.setattr(ask_ytm.queries, "cashflows_for", lambda isins: {})
    monkeypatch.setattr(ask_ytm.queries, "terms_for", lambda isins: {})

    def fake_schedule(isin, flows, terms, today):
        end = today + timedelta(days=365)
        return Schedule(isin, "plain", [Flow("interest", end, amount=120), Flow("redemption", end, amount=1000)],
                        face_value=1000, redemption_matches_face=True, periods_per_year=1, day_count="act/365")

    monkeypatch.setattr(ask_ytm.schedule, "build", fake_schedule)
    yield calls
    ask_ytm._cache.clear()


def test_best_ask_across_exchanges(env):
    [item] = ask_ytm.compute(1, ["INE000TEST01"])
    assert item["ask_price"] == 1005 and item["ask_qty"] == 10
    assert item["ytm"] == 11.44 and item["exchange"] == "BSE"
    assert item["ytm_method"] == "exact" and item["as_of"] == str(date.today())
    assert item["expires_at"] and item["fetched_at"] and item["calculation_version"]


def test_no_ask_is_cached_and_omitted(env):
    assert ask_ytm.compute(1, ["INE000TEST02"]) == []
    assert ask_ytm.compute(1, ["INE000TEST02"]) == []
    assert len(env["quotes"]) == 1  # second call served by negative cache


def test_cache_ttl_and_per_user_isolation(env, monkeypatch):
    ask_ytm.compute(1, ["INE000TEST01"])
    ask_ytm.compute(1, ["INE000TEST01"])
    assert len(env["quotes"]) == 1  # cached
    ask_ytm.compute(2, ["INE000TEST01"])
    assert len(env["quotes"]) == 2  # other user -> own fetch
    # Expire and refetch.
    key = (1, "INE000TEST01")
    ts, entry = ask_ytm._cache[key]
    ask_ytm._cache[key] = (ts - ask_ytm.CACHE_TTL_SECONDS - 1, entry)
    ask_ytm.compute(1, ["INE000TEST01"])
    assert len(env["quotes"]) == 3


def test_isin_cap_and_dedupe(env, monkeypatch):
    monkeypatch.setattr(ask_ytm, "MAX_ISINS", 2)
    items = ask_ytm.compute(1, ["INE000TEST01", "INE000TEST01",
                                "INE000TEST03", "INE000TEST02"])
    # deduped, capped at 2: TEST01 + TEST03 (TEST02 dropped by cap)
    assert {i["isin"] for i in items} == {"INE000TEST01", "INE000TEST03"}


def test_unusable_schedule_gives_null_ytm(env, monkeypatch):
    monkeypatch.setattr(ask_ytm.schedule, "build",
                        lambda isin, flows, terms, today: Schedule(isin, "empty"))
    [item] = ask_ytm.compute(1, ["INE000TEST03"])
    assert item["ask_price"] == 990.0 and item["ytm"] is None


def test_zero_size_and_expired_quotes_do_not_win_the_best_ask():
    quotes = {"NSE:T": _quote(900, 0), "BSE:T": _quote(1000, 5)}
    best = ask_ytm._best_ask(quotes, list(quotes))
    assert best is not None and best["ask_price"] == 1000
    quotes["NSE:T"] = {**_quote(900, 5), "timestamp": (datetime.now(UTC) - timedelta(minutes=5)).isoformat()}
    best = ask_ytm._best_ask(quotes, list(quotes))
    assert best is not None and best["ask_price"] == 1000


def test_cache_rejects_old_calculation_date_or_version(env):
    ask_ytm.compute(1, ["INE000TEST01"])
    ts, entry = ask_ytm._cache[(1, "INE000TEST01")]
    assert entry is not None
    ask_ytm._cache[(1, "INE000TEST01")] = (ts, {**entry, "as_of": "2000-01-01"})
    ask_ytm.compute(1, ["INE000TEST01"])
    assert len(env["quotes"]) == 2
    ts, entry = ask_ytm._cache[(1, "INE000TEST01")]
    assert entry is not None
    ask_ytm._cache[(1, "INE000TEST01")] = (ts, {**entry, "calculation_version": "obsolete"})
    ask_ytm.compute(1, ["INE000TEST01"])
    assert len(env["quotes"]) == 3


def test_steady_price_is_repriced_after_date_or_source_change(monkeypatch):
    today = date(2026, 7, 12)
    end = today + timedelta(days=365)
    source = [{"event": "Interest", "due_date": end, "amount_payable": 100},
              {"event": "Redemption", "due_date": end, "amount_redeem": 1000}]
    monkeypatch.setattr(ask_ytm.queries, "bond_cashflows", lambda _: source)
    monkeypatch.setattr(ask_ytm.queries, "bond_terms", lambda _: {"face_value": 1000, "redemption_date": end,
                        "interest_payment_frequency": "Annual", "day_count_convention": "Actual/365"})
    monkeypatch.setattr(ask_ytm.queries, "analytics_row", lambda *args: {})
    calculator = ask_ytm.PriceCalculator("TEST")
    [first] = calculator.values([1000], today)
    [next_day] = calculator.values([1000], today + timedelta(days=1))
    assert first["ytm"] != next_day["ytm"]
    source[0]["amount_payable"] = 120
    calculator.loaded_at -= 61
    [updated] = calculator.values([1000], today + timedelta(days=1))
    assert updated["ytm"] > next_day["ytm"]
    assert updated["schedule_version"] != next_day["schedule_version"]


def test_endpoint_empty_when_not_connected(monkeypatch):
    from fastapi.testclient import TestClient

    from backend import kite_connect
    from backend.app import app
    from backend.auth import require_user
    monkeypatch.setattr(kite_connect, "API_KEY", "k")
    monkeypatch.setattr(kite_connect, "API_SECRET", "s")
    monkeypatch.setattr(kite_connect, "token_for", lambda uid: None)
    app.dependency_overrides[require_user] = lambda: 1
    try:
        r = TestClient(app).post("/api/kite/ask-ytm",
                                 json={"isins": ["INE000TEST01"]})
        assert r.status_code == 200 and r.json() == {"items": []}
    finally:
        app.dependency_overrides.clear()
