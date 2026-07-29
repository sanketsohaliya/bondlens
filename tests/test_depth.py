"""Order-book depth parser and cache tests (no network)."""
from backend import depth


def _bse_payload(**overrides):
    base = {f"{side}{field}{i}": "--"
            for side in ("B", "S") for field in ("Price", "Qty")
            for i in range(1, 6)}
    base.update({"TotalBQty": "--", "TotalSQty": "--", "dttm": "29 Jul 2026"})
    base.update(overrides)
    return base


def test_parse_bse_depth_empty_is_none():
    assert depth.parse_bse_depth(_bse_payload()) is None


def test_parse_bse_depth_levels_and_commas():
    payload = _bse_payload(
        BPrice1="1,003.00", BQty1="25", BPrice2="1,001.50", BQty2="100",
        SPrice1="1,010.00", SQty1="10",
        TotalBQty="125", TotalSQty="10")
    side = depth.parse_bse_depth(payload)
    assert side is not None
    assert side["bids"] == [
        {"price": 1003.0, "qty": 25.0, "orders": None},
        {"price": 1001.5, "qty": 100.0, "orders": None},
    ]
    assert side["asks"] == [{"price": 1010.0, "qty": 10.0, "orders": None}]
    assert side["total_bid_qty"] == 125.0
    assert side["total_ask_qty"] == 10.0
    assert side["as_of"] == "29 Jul 2026"


def test_parse_bse_depth_one_sided_book():
    side = depth.parse_bse_depth(_bse_payload(SPrice1="990", SQty1="5"))
    assert side is not None
    assert side["bids"] == []
    assert side["asks"] == [{"price": 990.0, "qty": 5.0, "orders": None}]


def test_parse_nse_depth():
    payload = {"marketDeptOrderBook": {
        "totalBuyQuantity": 350,
        "totalSellQuantity": 60,
        "bid": [{"price": 1002.0, "quantity": 300},
                {"price": 1000.0, "quantity": 50},
                {"price": 0, "quantity": 0}],
        "ask": [{"price": 1011.0, "quantity": 60}],
    }}
    side = depth.parse_nse_depth(payload)
    assert side is not None
    assert len(side["bids"]) == 2
    assert side["bids"][0] == {"price": 1002.0, "qty": 300.0, "orders": None}
    assert side["asks"] == [{"price": 1011.0, "qty": 60.0, "orders": None}]
    assert side["total_bid_qty"] == 350.0
    assert side["total_ask_qty"] == 60.0


def test_parse_nse_depth_empty_is_none():
    assert depth.parse_nse_depth({}) is None
    assert depth.parse_nse_depth({"marketDeptOrderBook": {"bid": [], "ask": []}}) is None


def test_order_book_cache(monkeypatch):
    calls = {"bse": 0}

    def fake_bse(code):
        calls["bse"] += 1
        return {"bids": [], "asks": [{"price": 990.0, "qty": 5.0, "orders": None}],
                "total_bid_qty": None, "total_ask_qty": 5.0, "as_of": None}

    monkeypatch.setattr(depth, "fetch_bse_depth", fake_bse)
    depth._cache.clear()
    b1 = depth.order_book("INETEST00001", None, None, "974380")
    b2 = depth.order_book("INETEST00001", None, None, "974380")
    assert calls["bse"] == 1  # second call served from cache
    assert b1 is b2
    assert b1["nse"] is None  # no NSE symbol -> not fetched

    # Expired cache refetches.
    ts, book = depth._cache["INETEST00001"]
    depth._cache["INETEST00001"] = (ts - depth.CACHE_TTL_SECONDS - 1, book)
    depth.order_book("INETEST00001", None, None, "974380")
    assert calls["bse"] == 2
    depth._cache.clear()


def test_num_handles_junk():
    assert depth._num("--") is None
    assert depth._num("") is None
    assert depth._num(None) is None
    assert depth._num("1,00,290.50") == 100290.5
