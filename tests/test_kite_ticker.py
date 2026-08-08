"""Kite ticker binary parser and connection-manager tests (no network)."""
import asyncio
import json
import struct

from backend import kite_ticker


def _full_packet(token, ltp_paisa, total_buy, total_sell, bids, asks):
    """184-byte full-mode packet; bids/asks are (qty, price_paisa, orders)."""
    buf = bytearray(184)
    struct.pack_into(">ii", buf, 0, token, ltp_paisa)
    struct.pack_into(">ii", buf, 20, total_buy, total_sell)
    entries = list(bids) + [(0, 0, 0)] * (5 - len(bids))
    entries += list(asks) + [(0, 0, 0)] * (5 - len(asks))
    for i, (qty, price, orders) in enumerate(entries):
        struct.pack_into(">iih", buf, 64 + i * 12, qty, price, orders)
    return bytes(buf)


def _frame(*packets):
    out = struct.pack(">h", len(packets))
    for p in packets:
        out += struct.pack(">h", len(p)) + p
    return out


def test_parse_ticks_full_packet():
    pkt = _full_packet(
        408065, 100350, 125, 10,
        bids=[(25, 100300, 2), (100, 100150, 1)],
        asks=[(10, 101000, 1)])
    ticks = kite_ticker.parse_ticks(_frame(pkt))
    assert len(ticks) == 1
    t = ticks[0]
    assert t["token"] == 408065
    assert t["ltp"] == 1003.5
    assert t["total_bid_qty"] == 125.0
    assert t["total_ask_qty"] == 10.0
    assert t["bids"] == [
        {"price": 1003.0, "qty": 25.0, "orders": 2},
        {"price": 1001.5, "qty": 100.0, "orders": 1},
    ]
    assert t["asks"] == [{"price": 1010.0, "qty": 10.0, "orders": 1}]


def test_parse_ticks_multi_packet_and_skips_non_full():
    full = _full_packet(1, 100, 0, 0, bids=[(1, 100, 1)], asks=[])
    quote = bytes(44)  # quote-mode packet: no depth, skipped
    ticks = kite_ticker.parse_ticks(_frame(quote, full))
    assert [t["token"] for t in ticks] == [1]


def test_parse_ticks_heartbeat_and_junk():
    assert kite_ticker.parse_ticks(b"") == []
    assert kite_ticker.parse_ticks(b"\x00") == []
    assert kite_ticker.parse_ticks(struct.pack(">h", 3)) == []  # truncated


def test_tick_to_side():
    tick = {"token": 1, "ltp": 1.0, "total_bid_qty": 5.0,
            "total_ask_qty": None,
            "bids": [{"price": 1.0, "qty": 5.0, "orders": 1}], "asks": []}
    side = kite_ticker.tick_to_side(tick)
    assert side is not None
    assert side["source"] == "kite"
    assert side["total_bid_qty"] == 5.0
    empty = {**tick, "bids": [], "asks": []}
    assert kite_ticker.tick_to_side(empty) is None


class FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, msg):
        self.sent.append(json.loads(msg))


def test_userconn_refcounting():
    async def scenario():
        conn = kite_ticker._UserConn(1, "tok")
        conn.ws = FakeWS()
        q1 = await conn.attach({10, 20})
        q2 = await conn.attach({20, 30})
        # first attach subscribes 10+20; second only the new 30
        assert conn.ws.sent[0]["a"] == "subscribe"
        assert set(conn.ws.sent[0]["v"]) == {10, 20}
        assert conn.ws.sent[1]["a"] == "mode"
        assert set(conn.ws.sent[2]["v"]) == {30}
        assert conn.refcounts == {10: 1, 20: 2, 30: 1}

        # fan-out goes only to viewers of that token
        conn._fan_out({"token": 10, "x": 1})
        assert q1.qsize() == 1 and q2.qsize() == 0
        conn._fan_out({"token": 20, "x": 1})
        assert q1.qsize() == 2 and q2.qsize() == 1

        # detach q1: token 10 unsubscribed, 20 still refcounted by q2
        await conn.detach(q1)
        unsub = conn.ws.sent[-1]
        assert unsub["a"] == "unsubscribe" and set(unsub["v"]) == {10}
        assert conn.refcounts == {20: 1, 30: 1}
        await conn.detach(q2)
        assert conn.refcounts == {}
        assert conn.viewers == {}

    asyncio.run(scenario())


def test_manager_one_conn_per_user_and_teardown():
    async def scenario():
        mgr = kite_ticker.TickerManager()

        async def fake_run(self):
            await asyncio.sleep(3600)

        orig_run = kite_ticker._UserConn.run
        kite_ticker._UserConn.run = fake_run
        try:
            c1, q1 = await mgr.attach(1, "tok", {10})
            c2, q2 = await mgr.attach(1, "tok", {20})
            assert c1 is c2  # one connection per user
            c3, q3 = await mgr.attach(2, "tok2", {10})
            assert c3 is not c1
            await mgr.detach(1, c1, q1)
            assert 1 in mgr._conns  # still one viewer left
            await mgr.detach(1, c1, q2)
            assert 1 not in mgr._conns  # last viewer gone -> torn down
            assert c1.task is not None and c1.task.cancelled()
            await mgr.detach(2, c3, q3)
            assert not mgr._conns
        finally:
            kite_ticker._UserConn.run = orig_run

    asyncio.run(scenario())
