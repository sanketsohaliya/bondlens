"""Kite ticker WebSocket bridge: live order-book depth to the browser.

One outbound connection per connected user (opened lazily when they
have a depth ladder on screen, closed when the last one goes away),
subscribed in ``full`` mode to the viewed bonds' instrument tokens.
Parsed depth ticks fan out to per-viewer asyncio queues; app.py's
WS endpoint turns them into OrderBook-shaped JSON frames.

Kite's wire format is binary (big-endian):
    frame  = [n_packets:i16] ([length:i16][packet]) * n
    packet = token:i32, ltp:i32(paisa), ... ; ``full`` packets are 184
    bytes ending in 10 depth entries (5 bid + 5 ask) of
    qty:i32, price:i32(paisa), orders:i16, pad:i16.
1-byte frames are heartbeats; text frames are JSON error/postback
messages. Any connection failure just ends the affected viewers'
streams — the frontend falls back to the REST snapshot path.
"""
import asyncio
import contextlib
import json
import logging
import struct
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import websockets

from . import kite_connect

log = logging.getLogger("kite_ticker")

WS_URL = "wss://ws.kite.trade?api_key={api_key}&access_token={access_token}"

_FULL_PACKET_LEN = 184
_QUOTE_PACKET_LEN = 44
_DEPTH_OFFSET = 64  # bytes before the 10 depth entries in a full packet


def parse_ticks(frame: bytes) -> list[dict[str, Any]]:
    """Binary frame -> [{token, ltp, total_bid_qty, total_ask_qty,
    bids, asks}] for full-mode packets (others are skipped)."""
    if len(frame) < 4:
        return []
    (n,) = struct.unpack_from(">h", frame, 0)
    ticks, pos = [], 2
    for _ in range(n):
        if pos + 2 > len(frame):
            break
        (plen,) = struct.unpack_from(">h", frame, pos)
        pos += 2
        packet = frame[pos:pos + plen]
        pos += plen
        if len(packet) != _FULL_PACKET_LEN:
            continue  # index/quote-mode packets carry no usable depth
        token, ltp = struct.unpack_from(">ii", packet, 0)
        total_buy, total_sell = struct.unpack_from(">ii", packet, 20)
        bids: list[dict[str, Any]] = []
        asks: list[dict[str, Any]] = []
        for i in range(10):
            qty, price, orders = struct.unpack_from(
                ">iih", packet, _DEPTH_OFFSET + i * 12)
            if not qty and not price:
                continue
            level = {"price": price / 100 if price else None,
                     "qty": float(qty) if qty else None,
                     "orders": orders or None}
            (bids if i < 5 else asks).append(level)
        exchange_time = struct.unpack_from(">I", packet, 60)[0]
        ticks.append({
            "exchange_timestamp": datetime.fromtimestamp(exchange_time, UTC).isoformat() if exchange_time else None,
            "token": token,
            "ltp": ltp / 100 if ltp else None,
            "total_bid_qty": float(total_buy) if total_buy else None,
            "total_ask_qty": float(total_sell) if total_sell else None,
            "bids": bids,
            "asks": asks,
        })
    return ticks


def tick_to_side(tick: dict[str, Any]) -> dict[str, Any] | None:
    """Tick -> DepthSide dict (None when the book is empty)."""
    if not tick["bids"] and not tick["asks"]:
        return None
    return {"bids": tick["bids"], "asks": tick["asks"],
            "total_bid_qty": tick["total_bid_qty"],
            "total_ask_qty": tick["total_ask_qty"],
            "as_of": tick.get("exchange_timestamp"), "source": "kite"}


class _UserConn:
    """One Kite WS connection for one user, with refcounted token
    subscriptions and fan-out queues per attached viewer - or, for the
    opportunity watcher, a direct ``sink`` callback: subscribing 3,000
    instruments makes Kite answer with a snapshot frame of thousands of
    ticks, which no bounded viewer queue survives, and the watcher's
    per-tick work is pure CPU anyway."""

    def __init__(self, user_id: int, access_token: str, *,
                 sink: Callable[[dict[str, Any]], None] | None = None):
        self.user_id = user_id
        self.access_token = access_token
        self.sink = sink
        self.refcounts: dict[int, int] = {}
        self.viewers: dict[asyncio.Queue, set[int]] = {}
        self.ws: Any = None
        self.task: asyncio.Task | None = None

    async def run(self) -> None:
        url = WS_URL.format(api_key=kite_connect.API_KEY,
                            access_token=self.access_token)
        deliver = self.sink or self._fan_out
        try:
            async with websockets.connect(url, max_size=2 ** 20) as ws:
                self.ws = ws
                if self.refcounts:
                    await self._send_subscribe(list(self.refcounts))
                async for msg in ws:
                    if isinstance(msg, bytes):
                        if len(msg) <= 1:
                            continue  # heartbeat
                        for tick in parse_ticks(msg):
                            deliver(tick)
        except Exception as exc:  # noqa: BLE001 - any WS failure ends the stream
            # Never silent: a swallowed error here once hid a reconnect
            # storm for a whole session.
            log.warning("kite ticker for user %s ended: %r", self.user_id, exc)
        finally:
            self.ws = None
            for queue in self.viewers:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(None)  # sentinel: stream over

    async def _send_subscribe(self, tokens: list[int]) -> None:
        if self.ws is None or not tokens:
            return
        await self.ws.send(json.dumps({"a": "subscribe", "v": tokens}))
        await self.ws.send(json.dumps({"a": "mode", "v": ["full", tokens]}))

    async def _send_unsubscribe(self, tokens: list[int]) -> None:
        if self.ws is None or not tokens:
            return
        await self.ws.send(json.dumps({"a": "unsubscribe", "v": tokens}))

    def _fan_out(self, tick: dict[str, Any]) -> None:
        for queue, tokens in self.viewers.items():
            if tick["token"] in tokens:
                try:
                    queue.put_nowait(tick)
                except asyncio.QueueFull:
                    pass  # a slow viewer loses a tick; the socket must live on

    async def watch(self, tokens: set[int]) -> None:
        """Queue-less subscription set for sink users: subscribe what is
        new, unsubscribe what went away. Before the socket is open the
        refcounts alone suffice - run() subscribes them on connect."""
        current = set(self.refcounts)
        fresh, stale = tokens - current, current - tokens
        for t in fresh:
            self.refcounts[t] = 1
        for t in stale:
            del self.refcounts[t]
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self._send_subscribe(sorted(fresh))
                await self._send_unsubscribe(sorted(stale))

    async def attach(self, tokens: set[int]) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.viewers[queue] = tokens
        fresh = [t for t in tokens if not self.refcounts.get(t)]
        for t in tokens:
            self.refcounts[t] = self.refcounts.get(t, 0) + 1
        with contextlib.suppress(Exception):
            await self._send_subscribe(fresh)
        return queue

    async def detach(self, queue: asyncio.Queue) -> None:
        tokens = self.viewers.pop(queue, set())
        stale = []
        for t in tokens:
            self.refcounts[t] -= 1
            if not self.refcounts[t]:
                del self.refcounts[t]
                stale.append(t)
        with contextlib.suppress(Exception):
            await self._send_unsubscribe(stale)


class TickerManager:
    """Lazily-created per-user Kite connections, torn down when the
    last viewer detaches."""

    def __init__(self):
        self._conns: dict[int, _UserConn] = {}
        self._lock = asyncio.Lock()

    async def attach(self, user_id: int, access_token: str,
                     tokens: set[int]) -> tuple[_UserConn, asyncio.Queue]:
        async with self._lock:
            conn = self._conns.get(user_id)
            if conn is None or conn.task is None or conn.task.done():
                conn = _UserConn(user_id, access_token)
                conn.task = asyncio.create_task(conn.run())
                self._conns[user_id] = conn
            queue = await conn.attach(tokens)
            return conn, queue

    async def detach(self, user_id: int, conn: _UserConn,
                     queue: asyncio.Queue) -> None:
        async with self._lock:
            await conn.detach(queue)
            if not conn.viewers:
                if conn.task is not None:
                    conn.task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await conn.task
                if self._conns.get(user_id) is conn:
                    del self._conns[user_id]


manager = TickerManager()
