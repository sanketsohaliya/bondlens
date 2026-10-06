"""Opportunity watcher: Kite ticker in, ntfy pushes out.

One long-running asyncio process (its own container). For every user
with a live Kite token and at least one enabled rule or exit target it
keeps up to three ticker WebSockets open during market hours, subscribed
in ``full`` mode to every bond the rules' static filters select (the
whole universe fits: ~7,000 listings, 3,000 per socket). Each depth tick
is a dictionary lookup and a few float comparisons against thresholds
that were turned from yields into prices ahead of time
(threshold_cache); a hit pushes to ntfy FIRST and persists afterwards.

Timeline (IST, Mon-Fri):
  08:30  build/refresh thresholds for the day (also a cron slot)
  09:00  connect; Kite sends a snapshot tick per instrument, so pre-open
         resting orders are judged at once
  09:15  users with rules but no live token get one nudge
  every 10 min  refresh candidates/thresholds, apply subscription deltas
  15:45  disconnect; heartbeat keeps beating with note 'idle'
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import statistics
import time
from collections import deque
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

from . import (
    ask_ytm,
    db,
    kite,
    kite_connect,
    kite_ticker,
    ntfy,
    queries,
    threshold_cache,
)
from . import opportunities as opp
from . import opportunity_rules as rules_db

log = logging.getLogger("watcher")
IST = ZoneInfo("Asia/Kolkata")

OPEN = dtime(9, 0)
NUDGE_AT = dtime(9, 15)
CLOSE = dtime(15, 45)
PREP = dtime(8, 30)
REFRESH_SECONDS = 600
CHANGE_POLL_SECONDS = 5
HEARTBEAT_SECONDS = 30
TOKENS_PER_CONN = 3000
MAX_CONNS = 3
RECONNECT_MIN, RECONNECT_MAX = 1.0, 30.0
DOMAIN = os.environ.get("DOMAIN") or "bondlens.in"


def now_ist() -> datetime:
    return datetime.now(IST)


def market_phase(t: datetime) -> str:
    """'closed' | 'prep' | 'open' for an IST datetime."""
    if t.weekday() >= 5:
        return "closed"
    clock = t.timetz().replace(tzinfo=None)
    if OPEN <= clock < CLOSE:
        return "open"
    if PREP <= clock < OPEN:
        return "prep"
    return "closed"


def seconds_until(t: datetime, clock: dtime) -> float:
    """Seconds from `t` to the next weekday occurrence of `clock` (IST)."""
    target = datetime.combine(t.date(), clock, tzinfo=IST)
    while target <= t or target.weekday() >= 5:
        target += timedelta(days=1)
        target = datetime.combine(target.date(), clock, tzinfo=IST)
    return (target - t).total_seconds()


class Latency:
    """Ring buffer of tick->push-accepted milliseconds for the heartbeat."""

    def __init__(self, size: int = 200):
        self.samples: deque[int] = deque(maxlen=size)

    def add(self, ms: int) -> None:
        self.samples.append(ms)

    def percentiles(self) -> tuple[int | None, int | None]:
        if not self.samples:
            return None, None
        s = sorted(self.samples)
        q = statistics.quantiles(s, n=100, method="inclusive") if len(s) > 1 else [s[0]] * 99
        return int(q[49]), int(q[98])


class UserSession:
    """Everything the watcher holds for one user on one trading day."""

    def __init__(self, user_id: int, topic: str | None, pusher: ntfy.Pusher, latency: Latency):
        self.user_id = user_id
        self.topic = topic
        self.pusher = pusher
        self.latency = latency
        self.today: date = now_ist().date()
        self.thresholds = opp.Thresholds()
        self.token_map: dict[int, tuple[str, str]] = {}   # token -> (isin, exchange)
        self.bonds: dict[str, dict] = {}
        self.dedupe: dict[tuple[str, str], opp.Hit] = {}
        self.conns: list[tuple[kite_ticker._UserConn, set[int]]] = []
        self._closed: set[int] = set()   # id() of sockets we closed on purpose
        self.tasks: list[asyncio.Task] = []
        self.pending_pairs = 0
        self.access_token: str | None = None
        self.ytm_calc: dict[str, ask_ytm.PriceCalculator] = {}

    # -- preparation (runs in a thread; DB + valuation) ----------------------

    def prepare(self) -> dict[str, Any]:
        rules = [r for r in rules_db.all_enabled_rules() if r["user_id"] == self.user_id]
        targets = [t for t in rules_db.all_enabled_targets() if t["user_id"] == self.user_id]
        candidates = rules_db.candidates_for(rules)
        prices, solved = threshold_cache.ensure(rules, candidates, targets, self.today)
        thresholds, missing = opp.build_thresholds(rules, candidates, targets, prices)
        isins = sorted(thresholds.isins)
        symbols = queries.bonds_symbols(isins)
        token_map: dict[int, tuple[str, str]] = {}
        for isin in isins:
            row = symbols.get(isin) or {}
            if nse := kite.resolve_nse(row.get("nse_symbol"), row.get("nse_series")):
                token_map[nse[1]] = (isin, "NSE")
            if bse := kite.resolve_bse(row.get("bse_scrip_id")):
                token_map[bse[1]] = (isin, "BSE")
        return {"thresholds": thresholds, "token_map": token_map,
                "bonds": queries.bonds_brief(isins), "missing": len(missing), "solved": solved,
                "dedupe": rules_db.todays_hits(self.user_id, self.today),
                "topic": rules_db.get_topic(self.user_id),
                "token": db.get_kite_token(self.user_id)}

    def apply(self, prepared: dict[str, Any]) -> None:
        self.thresholds = prepared["thresholds"]
        self.token_map = prepared["token_map"]
        self.bonds = prepared["bonds"]
        self.pending_pairs = prepared["missing"]
        self.topic = prepared["topic"]
        self.access_token = prepared["token"]
        for (source, isin), row in prepared["dedupe"].items():
            self.dedupe.setdefault((source, isin), opp.Hit(
                source=source, isin=isin, side=row["side"], price=float(row["price"]),
                qty=float(row["qty"] or 0), exchange=row.get("exchange"), ytm=row.get("ytm")))

    # -- connections ----------------------------------------------------------

    def desired_token_sets(self) -> list[set[int]]:
        tokens = sorted(self.token_map)
        if len(tokens) > TOKENS_PER_CONN * MAX_CONNS:
            log.warning("user %s: %d tokens exceed %d; watching the first %d",
                        self.user_id, len(tokens), TOKENS_PER_CONN * MAX_CONNS,
                        TOKENS_PER_CONN * MAX_CONNS)
            tokens = tokens[:TOKENS_PER_CONN * MAX_CONNS]
        return [set(tokens[i:i + TOKENS_PER_CONN]) for i in range(0, len(tokens), TOKENS_PER_CONN)]

    async def reconcile_connections(self) -> None:
        """Make the open sockets match the desired token sets (deltas only)."""
        desired = self.desired_token_sets()
        if self.access_token is None:
            await self.disconnect()
            return
        # Grow/shrink the list of connections.
        while len(self.conns) > len(desired):
            conn, _ = self.conns.pop()
            await self._close(conn)
        for i, tokens in enumerate(desired):
            if i < len(self.conns):
                conn, current = self.conns[i]
                if current == tokens:
                    continue
                await conn.watch(tokens)
                self.conns[i] = (conn, tokens)
            else:
                self.conns.append((await self._open(tokens, i), tokens))
                self.tasks.append(asyncio.create_task(self._supervise(i)))

    async def _open(self, tokens: set[int], index: int) -> kite_ticker._UserConn:
        """A socket whose ticks run straight into on_tick (no queue: Kite's
        subscribe snapshot is thousands of ticks in one frame)."""
        assert self.access_token is not None
        conn = kite_ticker._UserConn(self.user_id, self.access_token, sink=self.on_tick)
        await conn.watch(tokens)
        conn.task = asyncio.create_task(conn.run())
        log.info("user %s: ticker socket %d connecting with %d tokens",
                 self.user_id, index, len(tokens))
        return conn

    async def _supervise(self, index: int) -> None:
        """Reconnect socket `index` when it ends, with backoff that only
        resets once a connection has proven itself (60 s up)."""
        backoff = RECONNECT_MIN
        while index < len(self.conns):
            conn, tokens = self.conns[index]
            if conn.task is None:
                return
            started = time.monotonic()
            # Three ways out of this await: the socket died (reconnect), we
            # closed it on purpose (stop), or this supervisor itself is being
            # cancelled (propagate).
            try:
                await conn.task
            except asyncio.CancelledError:
                me = asyncio.current_task()
                if me is not None and me.cancelling():
                    raise
            except Exception:  # noqa: BLE001 - run() already logged it
                pass
            if (id(conn) in self._closed or self.access_token is None
                    or index >= len(self.conns) or self.conns[index][0] is not conn):
                return
            if time.monotonic() - started >= 60:
                backoff = RECONNECT_MIN
            log.warning("user %s: ticker socket %d closed after %.0fs; reconnecting in %.0fs",
                        self.user_id, index, time.monotonic() - started, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)
            if index >= len(self.conns) or self.access_token is None:
                return
            tokens = self.conns[index][1]
            self.conns[index] = (await self._open(tokens, index), tokens)

    async def _close(self, conn: kite_ticker._UserConn) -> None:
        self._closed.add(id(conn))  # tell the supervisor this one is on purpose
        if conn.task is not None:
            conn.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await conn.task

    async def disconnect(self) -> None:
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.tasks.clear()
        for conn, _ in self.conns:
            await self._close(conn)
        self.conns.clear()

    # -- the hot path -----------------------------------------------------------

    def on_tick(self, tick: dict[str, Any]) -> None:
        """Pure comparisons; anything slow is handed to fire() as a task."""
        received = time.perf_counter()
        where = self.token_map.get(tick["token"])
        if where is None:
            return
        isin, exchange = where
        rules = self.thresholds.asks.get(isin)
        if rules and (ask := opp.best_ask(tick["asks"])):
            price, qty = ask
            for rule in opp.match_ask(rules, price, qty):
                hit = opp.Hit(source=f"rule:{rule.rule_id}", isin=isin, side="ask", price=price,
                              qty=qty, exchange=exchange, rule_name=rule.name,
                              ytm_bound=rule.min_ask_ytm)
                self._maybe_fire(hit, received, tick.get("exchange_timestamp"))
        target = self.thresholds.bids.get(isin)
        if target and (bid := opp.best_bid(tick["bids"])):
            price, qty = bid
            if opp.match_bid(target, price):
                hit = opp.Hit(source="target", isin=isin, side="bid", price=price, qty=qty,
                              exchange=exchange, ytm_bound=target.max_bid_ytm)
                self._maybe_fire(hit, received, tick.get("exchange_timestamp"))

    def _maybe_fire(self, hit: opp.Hit, received: float, exchange_ts: str | None) -> None:
        key = (hit.source, hit.isin)
        previous = self.dedupe.get(key)
        # Yield is unknown on the hot path; judge the re-alert on price. The
        # ledger entry (with yield) replaces this placeholder after fire().
        if previous is not None and not opp.should_fire(previous, hit):
            return
        self.dedupe[key] = hit
        asyncio.get_running_loop().create_task(self.fire(hit, received, exchange_ts))

    async def fire(self, hit: opp.Hit, received: float, exchange_ts: str | None) -> None:
        tick_at = quote_ts(exchange_ts)
        bond = self.bonds.get(hit.isin, {})
        title, body, url = opp.render(hit, bond, domain=DOMAIN, tick_at=tick_at)
        push_ms: int | None = None
        pushed_at = None
        if self.topic:
            try:
                await self.pusher.push_async(self.topic, title, body, priority=ntfy.PRIORITY_URGENT,
                                             tags="moneybag,bondlens", click=url)
                push_ms = int((time.perf_counter() - received) * 1000)
                pushed_at = datetime.now(UTC)
                self.latency.add(push_ms)
            except Exception as exc:  # noqa: BLE001 - never let a push failure stop the watcher
                log.warning("user %s: push failed for %s: %s", self.user_id, hit.isin, exc)
        # Slow parts off the loop: exact yield for the record, bell row, ledger.
        loop = asyncio.get_running_loop()
        full = await loop.run_in_executor(None, self._persist, hit, title, body, tick_at, pushed_at, push_ms)
        self.dedupe[(hit.source, hit.isin)] = full

    def _persist(self, hit: opp.Hit, title: str, body: str, tick_at: datetime | None,
                 pushed_at: datetime | None, push_ms: int | None) -> opp.Hit:
        ytm = None
        try:
            calc = self.ytm_calc.setdefault(hit.isin, ask_ytm.PriceCalculator(hit.isin))
            [valued] = calc.values([hit.price], self.today)
            ytm = valued.get("ytm")
        except Exception as exc:  # noqa: BLE001
            log.warning("user %s: yield for %s failed: %s", self.user_id, hit.isin, exc)
        full = opp.Hit(**{**hit.__dict__, "ytm": ytm})
        if ytm is not None:
            title, body, _ = opp.render(full, self.bonds.get(hit.isin, {}), domain=DOMAIN, tick_at=tick_at)
        kind = "ask_opportunity" if hit.side == "ask" else "bid_opportunity"
        day = self.today.isoformat()
        # Safety net: the push judged a precomputed price; the exact yield
        # must agree with the rule. A shortfall is recorded, not belled.
        short = opp.shortfall(full)
        if short is not None:
            log.warning("user %s: %s at %.2f yields %.2f%%, short of the rule by %.2f pt",
                        self.user_id, hit.isin, hit.price, full.ytm or 0, short)
        try:
            if short is None:
                db.add_notification(self.user_id, kind, "info", hit.isin, title, body,
                                    f"opp:{day}:{hit.source}:{hit.isin}:{hit.price:.2f}")
            rules_db.record_hit(self.user_id, full, self.today, tick_at=tick_at,
                                pushed_at=pushed_at, push_ms=push_ms, below_threshold=short)
        except Exception as exc:  # noqa: BLE001
            log.warning("user %s: persisting %s failed: %s", self.user_id, hit.isin, exc)
        return full

    # -- nudge ------------------------------------------------------------------

    def nudge_if_disconnected(self) -> None:
        if self.access_token is not None or not self.topic or not self.thresholds.isins:
            return
        key = ("nudge", "")
        if key in self.dedupe:
            return
        self.dedupe[key] = opp.Hit(source="nudge", isin="", side="ask", price=0, qty=0, exchange=None)
        try:
            self.pusher.push(self.topic, "Connect Kite to arm today's alerts",
                             "Your alert rules are waiting on a Kite login for today.",
                             priority=ntfy.PRIORITY_URGENT, tags="key,bondlens",
                             click=f"https://{DOMAIN}/")
        except Exception as exc:  # noqa: BLE001
            log.warning("user %s: nudge failed: %s", self.user_id, exc)


def quote_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class Watcher:
    def __init__(self, pusher: ntfy.Pusher | None = None):
        self.pusher = pusher or ntfy.Pusher()
        self.latency = Latency()
        self.sessions: dict[int, UserSession] = {}
        self.thresholds_ready = False
        self.note = "starting"
        self._changes_seen = datetime.now(UTC)

    async def heartbeat_forever(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            p50, p99 = self.latency.percentiles()
            users = len(self.sessions)
            tokens = sum(len(s.token_map) for s in self.sessions.values())
            beat = functools.partial(rules_db.heartbeat, users, tokens, p50=p50, p99=p99,
                                     thresholds_ready=self.thresholds_ready, note=self.note)
            with contextlib.suppress(Exception):
                await loop.run_in_executor(None, beat)
            await asyncio.sleep(HEARTBEAT_SECONDS)

    def eligible_users(self) -> list[int]:
        with_rules = {r["user_id"] for r in rules_db.all_enabled_rules()}
        with_targets = {t["user_id"] for t in rules_db.all_enabled_targets()}
        return sorted(with_rules | with_targets)

    async def refresh(self) -> None:
        """(Re)build every eligible user's session; connect when open."""
        loop = asyncio.get_running_loop()
        users = await loop.run_in_executor(None, self.eligible_users)
        for uid in list(self.sessions):
            if uid not in users:
                await self.sessions.pop(uid).disconnect()
        ready = True
        for uid in users:
            ready = await self.refresh_user(uid) and ready
        self.thresholds_ready = ready
        self.note = f"{market_phase(now_ist())}; {len(users)} user(s)"

    async def refresh_user(self, uid: int) -> bool:
        """Prepare/apply one user and reconcile their sockets. Returns
        whether every threshold they need is solved."""
        loop = asyncio.get_running_loop()
        session = self.sessions.get(uid)
        if session is None or session.today != now_ist().date():
            if session is not None:
                await session.disconnect()
            session = self.sessions[uid] = UserSession(uid, None, self.pusher, self.latency)
        try:
            prepared = await loop.run_in_executor(None, session.prepare)
        except Exception as exc:  # noqa: BLE001 - one user's data must not stop the others
            log.exception("user %s: prepare failed: %s", uid, exc)
            return False
        if prepared["token"] is not None and not await loop.run_in_executor(
                None, kite_connect.token_is_live, uid):
            prepared["token"] = None
        session.apply(prepared)
        if session.topic:
            await loop.run_in_executor(None, self.pusher.warm, session.topic)
        if market_phase(now_ist()) == "open":
            await session.reconcile_connections()
        log.info("user %s: %d bonds, %d tokens, %d thresholds pending, token %s",
                 uid, len(session.thresholds.isins), len(session.token_map),
                 prepared["missing"], "live" if session.access_token else "none")
        return prepared["missing"] == 0

    async def apply_changes(self) -> None:
        """Rules/targets/topic edits take effect within a poll, not a refresh."""
        loop = asyncio.get_running_loop()
        changed = await loop.run_in_executor(None, rules_db.changed_since, self._changes_seen)
        if not changed:
            return
        self._changes_seen = max(changed.values())
        users = set(await loop.run_in_executor(None, self.eligible_users))
        for uid in changed:
            if uid in users:
                log.info("user %s: alert setup changed; refreshing now", uid)
                self.note = f"solving thresholds for user {uid}"
                await self.refresh_user(uid)
            elif uid in self.sessions:
                log.info("user %s: no rules left; disconnecting", uid)
                await self.sessions.pop(uid).disconnect()
        self.note = f"{market_phase(now_ist())}; {len(users)} user(s)"

    async def run(self) -> None:
        asyncio.create_task(self.heartbeat_forever())
        while True:
            t = now_ist()
            phase = market_phase(t)
            if phase == "closed":
                for session in self.sessions.values():
                    await session.disconnect()
                self.note = "idle"
                wait = min(seconds_until(t, PREP), 3600)
                log.info("closed; next prep in %.0f min", wait / 60)
                await asyncio.sleep(wait)
                continue
            await self.refresh()
            if phase == "open" and t.timetz().replace(tzinfo=None) >= NUDGE_AT:
                for session in self.sessions.values():
                    session.nudge_if_disconnected()
            # Between full refreshes, poll the change marker every few seconds.
            boundary = seconds_until(t, OPEN if phase == "prep" else CLOSE)
            deadline = time.monotonic() + min(boundary, REFRESH_SECONDS)
            while time.monotonic() < deadline:
                await asyncio.sleep(min(CHANGE_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
                with contextlib.suppress(Exception):
                    await self.apply_changes()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not kite_connect.configured():
        log.error("KITE_API_KEY/SECRET not set; the watcher has nothing to connect with")
        return 2
    try:
        asyncio.run(Watcher().run())
    except KeyboardInterrupt:
        pass
    finally:
        db.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
