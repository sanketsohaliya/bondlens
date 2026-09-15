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
    monkeypatch.setattr(ask_ytm.time, "sleep", lambda s: None)  # no Kite pacing in tests
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
    assert item["fetched_at"] and item["calculation_version"]
    assert "expires_at" not in item


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


def test_zero_size_does_not_win_the_best_ask():
    quotes = {"NSE:T": _quote(900, 0), "BSE:T": _quote(1000, 5)}
    best = ask_ytm._best_ask(quotes, list(quotes))
    assert best is not None and best["ask_price"] == 1000


def test_an_unchanged_book_is_still_the_best_ask():
    # 15 Sept 2026: 202 listings had a resting ask, 12 had changed in the
    # last 90s. Kite's timestamp is the book's last change, not its
    # validity - an ask resting since the open is a live ask.
    since_open = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    quotes = {"NSE:T": {**_quote(900, 5), "timestamp": since_open}, "BSE:T": _quote(1000, 5)}
    best = ask_ytm._best_ask(quotes, list(quotes))
    assert best is not None and best["ask_price"] == 900
    assert {w["code"] for w in best["warnings"]} == {"book_unchanged"}


def test_a_future_dated_book_is_refused():
    ahead = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    quotes = {"NSE:T": {**_quote(900, 5), "timestamp": ahead}, "BSE:T": _quote(1000, 5)}
    best = ask_ytm._best_ask(quotes, list(quotes))
    assert best is not None and best["ask_price"] == 1000


def test_universe_is_paged_in_yield_order_and_cached(monkeypatch):
    calls = []
    universe = [f"INE{n:09d}" for n in range(2300)]
    monkeypatch.setattr(ask_ytm.queries, "ask_universe", lambda: calls.append(1) or list(universe))
    monkeypatch.setattr(ask_ytm, "_universe", None)
    pages = ask_ytm.universe_pages()
    assert [len(p) for p in pages] == [1000, 1000, 300]
    assert pages[0][0] == universe[0] and pages[2][-1] == universe[-1]
    ask_ytm.universe_pages()
    assert len(calls) == 1  # served from the 10-minute cache
    monkeypatch.setattr(ask_ytm, "_universe", None)


def test_compute_page_reports_its_isins_so_no_ask_can_be_recorded(env, monkeypatch):
    monkeypatch.setattr(ask_ytm, "_universe", None)
    monkeypatch.setattr(ask_ytm.queries, "ask_universe",
                        lambda: ["INE000TEST01", "INE000TEST02", "INE000TEST03"])
    monkeypatch.setattr(ask_ytm, "PAGE", 2)
    first = ask_ytm.compute_page(1, 0)
    assert first["page"] == 0 and first["pages"] == 2
    assert first["isins"] == ["INE000TEST01", "INE000TEST02"]
    assert [i["isin"] for i in first["items"]] == ["INE000TEST01"]  # TEST02 has no ask
    last = ask_ytm.compute_page(1, 1)
    assert last["isins"] == ["INE000TEST03"] and len(last["items"]) == 1
    assert ask_ytm.compute_page(1, 7) == {"items": [], "page": 7, "pages": 2, "isins": []}
    monkeypatch.setattr(ask_ytm, "_universe", None)


def test_quote_calls_are_paced_to_kites_one_per_second(monkeypatch):
    clock = {"t": 100.0}
    slept: list[float] = []
    monkeypatch.setattr(ask_ytm.time, "monotonic", lambda: clock["t"])

    def sleep(s):
        slept.append(s)
        clock["t"] += s
    monkeypatch.setattr(ask_ytm.time, "sleep", sleep)
    monkeypatch.setattr(ask_ytm.kite_connect, "fetch_quotes", lambda uid, inst: {})
    monkeypatch.setattr(ask_ytm, "_last_quote_call", 0.0)
    ask_ytm._paced_quotes(1, ["NSE:A"])
    assert slept == []  # first call goes straight out
    ask_ytm._paced_quotes(1, ["NSE:B"])
    assert len(slept) == 1 and abs(slept[0] - ask_ytm.QUOTE_MIN_INTERVAL) < 1e-9
    clock["t"] += 5
    ask_ytm._paced_quotes(1, ["NSE:C"])
    assert len(slept) == 1  # long enough gap, no wait


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
        assert r.status_code == 200 and r.json()["items"] == []
    finally:
        app.dependency_overrides.clear()
