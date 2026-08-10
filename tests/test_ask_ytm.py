"""YTM @ Ask computation tests (no network, no DB)."""
import pytest

from backend import ask_ytm


def _quote(ask_price, ask_qty):
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

    class FakeSched:
        usable = True

    monkeypatch.setattr(ask_ytm.schedule, "build",
                        lambda isin, flows, terms, today: FakeSched())
    monkeypatch.setattr(ask_ytm.yields, "exact_ytm",
                        lambda price, sched, today: round(1000 / price * 12, 2))
    yield calls
    ask_ytm._cache.clear()


def test_best_ask_across_exchanges(env):
    items = ask_ytm.compute(1, ["INE000TEST01"])
    assert items == [{"isin": "INE000TEST01", "ask_price": 1005.0,
                      "ask_qty": 10.0, "ytm": round(1000 / 1005 * 12, 2)}]


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
    class Unusable:
        usable = False

    monkeypatch.setattr(ask_ytm.schedule, "build",
                        lambda isin, flows, terms, today: Unusable())
    [item] = ask_ytm.compute(1, ["INE000TEST03"])
    assert item["ask_price"] == 990.0 and item["ytm"] is None


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
