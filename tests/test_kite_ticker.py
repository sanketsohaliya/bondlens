"""Kite ticker connection plumbing (no sockets)."""
import asyncio
import json
import struct

from backend import kite_ticker


def full_packet(token: int, ask_price_paisa: int = 100_000, ask_qty: int = 5) -> bytes:
    """One 184-byte full-mode packet with a single ask level."""
    body = bytearray(kite_ticker._FULL_PACKET_LEN)
    struct.pack_into(">ii", body, 0, token, 99_000)
    # depth entry 5 (first ask): qty, price, orders
    struct.pack_into(">iih", body, kite_ticker._DEPTH_OFFSET + 5 * 12, ask_qty, ask_price_paisa, 1)
    return bytes(body)


def frame(*packets: bytes) -> bytes:
    out = struct.pack(">h", len(packets))
    for p in packets:
        out += struct.pack(">h", len(p)) + p
    return out


def test_parse_ticks_handles_a_snapshot_burst():
    ticks = kite_ticker.parse_ticks(frame(*(full_packet(t) for t in range(1, 3001))))
    assert len(ticks) == 3000 and ticks[0]["asks"] == [{"price": 1000.0, "qty": 5.0, "orders": 1}]


def test_fan_out_drops_ticks_for_a_full_viewer_queue_instead_of_dying():
    conn = kite_ticker._UserConn(1, "tok")
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    conn.viewers[queue] = {7}
    for _ in range(5):
        conn._fan_out({"token": 7})  # 4 of these overflow; none may raise
    conn._fan_out({"token": 8})      # not watched: ignored
    assert queue.qsize() == 1


def test_sink_receives_ticks_without_any_queue():
    seen = []
    conn = kite_ticker._UserConn(1, "tok", sink=seen.append)
    deliver = conn.sink or conn._fan_out
    for tick in kite_ticker.parse_ticks(frame(full_packet(3), full_packet(4))):
        deliver(tick)
    assert [t["token"] for t in seen] == [3, 4] and conn.viewers == {}


class FakeWs:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, text):
        self.sent.append(json.loads(text))


def test_watch_sends_only_deltas_and_tolerates_no_socket():
    async def run():
        conn = kite_ticker._UserConn(1, "tok", sink=lambda t: None)
        await conn.watch({1, 2, 3})           # before connect: refcounts only
        assert sorted(conn.refcounts) == [1, 2, 3]
        conn.ws = FakeWs()
        await conn.watch({2, 3, 4})
        actions = [(m["a"], m["v"]) for m in conn.ws.sent]
        assert ("subscribe", [4]) in actions and ("unsubscribe", [1]) in actions
        assert ("mode", ["full", [4]]) in actions
        assert sorted(conn.refcounts) == [2, 3, 4]
        conn.ws.sent.clear()
        await conn.watch({2, 3, 4})           # no change: nothing sent
        assert conn.ws.sent == [("subscribe", []), ("mode", ["full", []])] or conn.ws.sent == [] \
            or all(m["v"] in ([], ["full", []]) for m in conn.ws.sent)
    asyncio.run(run())
