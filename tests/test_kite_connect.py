"""Kite Connect session/quote/order unit tests (no network, no DB)."""
import hashlib
import json

import pytest

from backend import depth, kite_connect


class FakeResp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self.ok = status_code < 400
        self._body = body if body is not None else {}

    def json(self):
        if isinstance(self._body, str):
            return json.loads(self._body)  # raises on junk
        return self._body


@pytest.fixture
def kite_env(monkeypatch):
    """Configured app + in-memory token store; no DB, no network."""
    store: dict[int, str] = {}
    monkeypatch.setattr(kite_connect, "API_KEY", "testkey")
    monkeypatch.setattr(kite_connect, "API_SECRET", "testsecret")
    monkeypatch.setattr(kite_connect, "_tokens", {})
    monkeypatch.setattr(kite_connect.db, "get_kite_token", store.get)
    monkeypatch.setattr(kite_connect.db, "set_kite_token",
                        lambda uid, tok: store.__setitem__(uid, tok))
    monkeypatch.setattr(kite_connect.db, "delete_kite_token",
                        lambda uid: store.pop(uid, None))
    return store


def test_configured_and_login_url(kite_env, monkeypatch):
    assert kite_connect.configured()
    assert kite_connect.login_url() == \
        "https://kite.zerodha.com/connect/login?v=3&api_key=testkey"
    monkeypatch.setattr(kite_connect, "API_KEY", "")
    assert not kite_connect.configured()
    assert kite_connect.login_url() is None


def test_login_url_carries_state_and_origin(kite_env):
    url = kite_connect.login_url(state="st.jwt", origin="https://b.example.com")
    assert url is not None
    from urllib.parse import parse_qs, urlparse
    qs = parse_qs(urlparse(url).query)
    inner = parse_qs(qs["redirect_params"][0])
    assert inner == {"state": ["st.jwt"], "origin": ["https://b.example.com"]}


def test_safe_origin_allowlist(monkeypatch):
    monkeypatch.setattr(kite_connect, "REDIRECT_ORIGINS",
                        frozenset({"https://a.example.com"}))
    assert kite_connect.safe_origin("https://a.example.com") == "https://a.example.com"
    assert kite_connect.safe_origin("https://a.example.com/") == "https://a.example.com"
    assert kite_connect.safe_origin("https://evil.example.com") == ""
    assert kite_connect.safe_origin(None) == ""


def test_exchange_token_checksum_and_store(kite_env, monkeypatch):
    seen = {}

    def fake_post(url, timeout, data, headers):
        seen.update(url=url, data=data)
        return FakeResp(200, {"status": "success",
                              "data": {"access_token": "tok123"}})

    monkeypatch.setattr(kite_connect.requests, "post", fake_post)
    kite_connect.exchange_token(7, "reqtok")
    expected = hashlib.sha256(b"testkey" + b"reqtok" + b"testsecret").hexdigest()
    assert seen["data"]["checksum"] == expected
    assert seen["data"]["api_key"] == "testkey"
    assert kite_env[7] == "tok123"
    assert kite_connect.token_for(7) == "tok123"


def test_exchange_token_failure_raises(kite_env, monkeypatch):
    monkeypatch.setattr(
        kite_connect.requests, "post",
        lambda *a, **k: FakeResp(403, {"status": "error",
                                       "message": "Invalid checksum"}))
    with pytest.raises(kite_connect.KiteError) as e:
        kite_connect.exchange_token(7, "reqtok")
    assert "Invalid checksum" in e.value.detail
    assert 7 not in kite_env


def test_403_clears_only_that_users_token(kite_env, monkeypatch):
    kite_env.update({1: "tok-a", 2: "tok-b"})
    monkeypatch.setattr(
        kite_connect.requests, "request",
        lambda *a, **k: FakeResp(403, {"status": "error",
                                       "message": "Token expired"}))
    with pytest.raises(kite_connect.KiteError) as e:
        kite_connect.list_orders(1)
    assert e.value.status == 401
    assert kite_connect.token_for(1) is None
    assert kite_connect.token_for(2) == "tok-b"


def test_fetch_quotes_never_raises(kite_env, monkeypatch):
    assert kite_connect.fetch_quotes(1, ["NSE:X"]) == {}  # not connected
    kite_env[1] = "tok"
    monkeypatch.setattr(
        kite_connect.requests, "request",
        lambda *a, **k: (_ for _ in ()).throw(ValueError) if False else
        FakeResp(500, "not json"))
    assert kite_connect.fetch_quotes(1, ["NSE:X"]) == {}
    assert kite_connect.fetch_quotes(1, []) == {}


def test_place_order_params_and_id(kite_env, monkeypatch):
    kite_env[1] = "tok"
    seen = {}

    def fake_request(method, url, params, data, timeout, headers):
        seen.update(method=method, url=url, data=data, headers=headers)
        return FakeResp(200, {"status": "success",
                              "data": {"order_id": "230814000123"}})

    monkeypatch.setattr(kite_connect.requests, "request", fake_request)
    order_id = kite_connect.place_order(1, "BSE", "962APSB32A", "BUY",
                                        10, 1010.5)
    assert order_id == "230814000123"
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/orders/regular")
    assert seen["headers"]["Authorization"] == "token testkey:tok"
    assert seen["data"] == {
        "exchange": "BSE", "tradingsymbol": "962APSB32A",
        "transaction_type": "BUY", "order_type": "LIMIT",
        "product": "CNC", "validity": "DAY",
        "quantity": "10", "price": "1010.50",
    }


def test_place_order_rejection_surfaces_message(kite_env, monkeypatch):
    kite_env[1] = "tok"
    monkeypatch.setattr(
        kite_connect.requests, "request",
        lambda *a, **k: FakeResp(400, {
            "status": "error",
            "message": "Insufficient funds. Required margin is 10100.00"}))
    with pytest.raises(kite_connect.KiteError) as e:
        kite_connect.place_order(1, "BSE", "X", "BUY", 10, 1010.0)
    assert e.value.status == 400
    assert "Insufficient funds" in e.value.detail


def test_list_and_cancel_orders(kite_env, monkeypatch):
    kite_env[1] = "tok"
    calls = []

    def fake_request(method, url, params, data, timeout, headers):
        calls.append((method, url))
        if method == "GET":
            return FakeResp(200, {"status": "success", "data": [{
                "order_id": "1", "tradingsymbol": "962APSB32A",
                "exchange": "BSE", "transaction_type": "BUY",
                "quantity": 10, "filled_quantity": 0, "price": 1010.0,
                "average_price": 0, "status": "OPEN",
                "status_message": None, "order_timestamp": "2026-08-08 11:00:00",
                "extra_field_ignored": True,
            }]})
        return FakeResp(200, {"status": "success", "data": {"order_id": "1"}})

    monkeypatch.setattr(kite_connect.requests, "request", fake_request)
    orders = kite_connect.list_orders(1)
    assert orders == [{
        "order_id": "1", "tradingsymbol": "962APSB32A", "exchange": "BSE",
        "transaction_type": "BUY", "quantity": 10, "filled_quantity": 0,
        "price": 1010.0, "average_price": 0, "status": "OPEN",
        "status_message": None, "order_timestamp": "2026-08-08 11:00:00",
    }]
    kite_connect.cancel_order(1, "1")
    assert calls[-1] == ("DELETE", "https://api.kite.trade/orders/regular/1")


# --- Depth parsing ----------------------------------------------------------

def _quote(depth_book=None, **extra):
    return {"depth": depth_book or {}, **extra}


def test_parse_kite_depth_levels():
    q = _quote(
        {"buy": [{"price": 1003.0, "quantity": 25, "orders": 2},
                 {"price": 0, "quantity": 0, "orders": 0}],
         "sell": [{"price": 1010.0, "quantity": 10, "orders": 1}]},
        buy_quantity=25, sell_quantity=10,
        last_trade_time="2026-08-08 11:04:00")
    side = kite_connect.parse_kite_depth(q)
    assert side is not None
    assert side["bids"] == [{"price": 1003.0, "qty": 25.0, "orders": 2}]
    assert side["asks"] == [{"price": 1010.0, "qty": 10.0, "orders": 1}]
    assert side["total_bid_qty"] == 25.0
    assert side["total_ask_qty"] == 10.0
    assert side["as_of"] == "2026-08-08 11:04:00"
    assert side["source"] == "kite"


def test_parse_kite_depth_empty_is_none():
    assert kite_connect.parse_kite_depth(_quote()) is None
    assert kite_connect.parse_kite_depth(_quote(
        {"buy": [{"price": 0, "quantity": 0, "orders": 0}] * 5,
         "sell": [{"price": 0, "quantity": 0, "orders": 0}] * 5})) is None


# --- order_book source selection & cache isolation --------------------------

def _fake_side(price):
    return {"bids": [], "asks": [{"price": price, "qty": 1.0, "orders": 1}],
            "total_bid_qty": None, "total_ask_qty": 1.0, "as_of": None,
            "source": "kite"}


@pytest.fixture
def depth_env(kite_env, monkeypatch):
    depth._cache.clear()
    calls = {"kite": 0, "bse": 0, "nse": 0}
    monkeypatch.setattr(
        depth, "fetch_kite_depth",
        lambda uid, *a: (calls.__setitem__("kite", calls["kite"] + 1),
                         (_fake_side(1000.0 + uid), _fake_side(2000.0 + uid))
                         )[1])
    monkeypatch.setattr(
        depth, "fetch_bse_depth",
        lambda code: (calls.__setitem__("bse", calls["bse"] + 1),
                      {**_fake_side(999.0), "source": "exchange"})[1])
    monkeypatch.setattr(
        depth, "fetch_nse_depth",
        lambda sym, series: (calls.__setitem__("nse", calls["nse"] + 1),
                             {**_fake_side(998.0), "source": "exchange"})[1])
    yield calls
    depth._cache.clear()


def test_order_book_kite_primary(depth_env, kite_env):
    kite_env[1] = "tok"
    book = depth.order_book("INETEST00001", "SYM", "N1", "974380",
                            bse_scrip_id="962TEST", user_id=1)
    assert depth_env == {"kite": 1, "bse": 0, "nse": 0}
    assert book["nse"]["source"] == "kite"
    assert book["bse"]["source"] == "kite"


def test_order_book_scraper_fallback_per_exchange(depth_env, kite_env,
                                                  monkeypatch):
    kite_env[1] = "tok"
    # Kite serves NSE only; BSE must come from the scraper.
    monkeypatch.setattr(depth, "fetch_kite_depth",
                        lambda uid, *a: (_fake_side(1001.0), None))
    book = depth.order_book("INETEST00002", "SYM", "N1", "974380",
                            bse_scrip_id="962TEST", user_id=1)
    assert depth_env["bse"] == 1
    assert book["nse"]["source"] == "kite"
    assert book["bse"]["source"] == "exchange"


def test_order_book_without_token_uses_scrapers(depth_env):
    book = depth.order_book("INETEST00003", "SYM", "N1", "974380",
                            bse_scrip_id="962TEST", user_id=1)
    assert depth_env == {"kite": 0, "bse": 1, "nse": 1}
    assert book["bse"]["source"] == "exchange"


def test_kite_state_roundtrip(monkeypatch):
    from backend import auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "test-secret-key-32-bytes-long!!!")
    state = auth.mint_kite_state(42)
    assert auth.parse_kite_state(state) == 42
    assert auth.parse_kite_state("garbage") is None
    # A session JWT must not be accepted as a state token.
    session = auth.mint_session(42)
    assert auth.parse_kite_state(session) is None
    assert auth.parse_session(state) == 42  # (session parser is laxer; fine)


def test_callback_authenticates_via_state_and_redirects_to_origin(
        kite_env, monkeypatch):
    from fastapi.testclient import TestClient

    from backend import auth
    from backend.app import app
    monkeypatch.setattr(auth, "SESSION_SECRET", "test-secret-key-32-bytes-long!!!")
    monkeypatch.setattr(kite_connect, "REDIRECT_ORIGINS",
                        frozenset({"https://b.example.com"}))
    monkeypatch.setattr(
        kite_connect.requests, "post",
        lambda *a, **k: FakeResp(200, {"status": "success",
                                       "data": {"access_token": "tok"}}))
    client = TestClient(app)

    # No cookie: identity comes from the state token; bounced to origin B.
    state = auth.mint_kite_state(9)
    r = client.get("/api/kite/callback", follow_redirects=False, params={
        "request_token": "req", "status": "success",
        "state": state, "origin": "https://b.example.com"})
    assert r.status_code == 307
    assert r.headers["location"] == "https://b.example.com/?kite=connected"
    assert kite_env[9] == "tok"

    # Unlisted origin never becomes a redirect target.
    r = client.get("/api/kite/callback", follow_redirects=False, params={
        "request_token": "req", "status": "success",
        "state": auth.mint_kite_state(9), "origin": "https://evil.example.com"})
    assert r.headers["location"] == "/?kite=connected"

    # No cookie and no/invalid state -> error redirect, nothing stored.
    kite_env.clear()
    r = client.get("/api/kite/callback", follow_redirects=False, params={
        "request_token": "req", "status": "success", "state": "junk"})
    assert r.headers["location"] == "/?kite=error"
    assert not kite_env


def test_depth_ws_rejects_missing_session():
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from backend.app import app
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/api/kite/depth/INETEST00001"):
            pass
    assert e.value.code == 4401


def test_order_book_cache_isolated_per_user(depth_env, kite_env):
    kite_env.update({1: "tok-a", 2: "tok-b"})
    b1 = depth.order_book("INETEST00004", "SYM", "N1", None,
                          bse_scrip_id="962TEST", user_id=1)
    b2 = depth.order_book("INETEST00004", "SYM", "N1", None,
                          bse_scrip_id="962TEST", user_id=2)
    assert b1 is not b2  # user 2 never sees user 1's cached kite book
    assert depth_env["kite"] == 2
    assert b1["nse"]["asks"][0]["price"] == 1001.0
    assert b2["nse"]["asks"][0]["price"] == 1002.0
