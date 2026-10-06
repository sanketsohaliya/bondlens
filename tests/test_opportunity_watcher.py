"""Watcher behaviour with Kite, ntfy and the DB faked (no network)."""
import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend import health
from backend import opportunities as opp
from backend import opportunity_watcher as w

IST = ZoneInfo("Asia/Kolkata")


def ist(h, m=0, day=6):  # 2026-10-06 is a Tuesday
    return datetime(2026, 10, day, h, m, tzinfo=IST)


# --- schedule ------------------------------------------------------------------

def test_market_phases_and_countdowns():
    assert w.market_phase(ist(8, 29)) == "closed"
    assert w.market_phase(ist(8, 30)) == "prep"
    assert w.market_phase(ist(9, 0)) == "open"
    assert w.market_phase(ist(15, 44)) == "open"
    assert w.market_phase(ist(15, 45)) == "closed"
    assert w.market_phase(ist(11, 0, day=10)) == "closed"   # Saturday
    assert w.seconds_until(ist(8, 0), w.PREP) == 30 * 60
    # Friday evening -> Monday 08:30
    assert w.seconds_until(ist(16, 0, day=9), w.PREP) == (2 * 24 + 16) * 3600 + 30 * 60


def test_latency_percentiles():
    lat = w.Latency()
    assert lat.percentiles() == (None, None)
    for ms in range(1, 101):
        lat.add(ms)
    p50, p99 = lat.percentiles()
    assert p50 is not None and p99 is not None
    assert 49 <= p50 <= 51 and 98 <= p99 <= 100


# --- hot path --------------------------------------------------------------------

class FakePusher:
    def __init__(self, fail=False):
        self.calls: list[dict] = []
        self.fail = fail

    def push(self, topic, title, body, **kw):
        if self.fail:
            raise RuntimeError("ntfy down")
        self.calls.append({"topic": topic, "title": title, "body": body, **kw})
        return 7

    async def push_async(self, topic, title, body, **kw):
        return self.push(topic, title, body, **kw)

    def warm(self, topic):
        pass


def make_session(monkeypatch, pusher=None):
    pusher = pusher or FakePusher()
    s = w.UserSession(1, "topic-abcdef", pusher, w.Latency())  # type: ignore[arg-type]
    s.thresholds = opp.Thresholds(
        asks={"INE000A": [opp.AskRule(rule_id=7, name="BBB 12%", max_ask_price=950.0,
                                      max_price=None, max_ticket=None, min_qty=1.0)]},
        bids={"INE000H": opp.BidTarget(min_bid_price=1010.0)})
    s.token_map = {101: ("INE000A", "BSE"), 202: ("INE000H", "NSE")}
    s.bonds = {"INE000A": {"issuer_name": "ALPHA LTD", "bse_scrip_id": "ALPHA27"},
               "INE000H": {"issuer_name": "HELD LTD", "nse_symbol": "HELD"}}
    recorded: list = []
    monkeypatch.setattr(w.db, "add_notification",
                        lambda *a, **k: recorded.append(("bell", a[1], a[3], a[4])) or True)
    monkeypatch.setattr(w.rules_db, "record_hit",
                        lambda uid, hit, today, **k: recorded.append(("ledger", hit.source, hit.isin, hit.ytm, k["push_ms"])) or 1)

    class Calc:
        def __init__(self, isin):
            pass

        def values(self, prices, today):
            return [{"ytm": 12.4}]
    monkeypatch.setattr(w.ask_ytm, "PriceCalculator", Calc)
    return s, pusher, recorded


def tick(token, asks=(), bids=()):
    return {"token": token, "asks": [{"price": p, "qty": q} for p, q in asks],
            "bids": [{"price": p, "qty": q} for p, q in bids],
            "exchange_timestamp": "2026-10-06T04:01:41+00:00"}


async def settle():
    """Let fire() tasks and their executor hops finish."""
    for _ in range(40):
        await asyncio.sleep(0.01)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        if not pending:
            return


def test_ask_hit_pushes_first_then_records_and_dedupes(monkeypatch):
    async def run():
        s, pusher, recorded = make_session(monkeypatch)
        s.on_tick(tick(101, asks=[(960.0, 5), (940.0, 3)]))   # best ask 940 <= 950
        await settle()
        assert len(pusher.calls) == 1
        call = pusher.calls[0]
        assert call["topic"] == "topic-abcdef" and call["priority"] == "5"
        assert call["title"] == "Ask ₹940.00 × 3 — ALPHA LTD"   # yield not yet known on the hot path
        assert call["click"] == "https://bondlens.in/?bond=INE000A&tab=order%20book"
        assert [r[0] for r in recorded] == ["bell", "ledger"]
        assert recorded[0][2] == "INE000A" and recorded[0][3].startswith("12.40% @ ₹940.00 × 3")
        assert recorded[1][3] == 12.4 and recorded[1][4] is not None
        # Same quote again: nothing. A cheaper ask by >= 0.5 %: fires again.
        s.on_tick(tick(101, asks=[(940.0, 3)]))
        s.on_tick(tick(101, asks=[(937.0, 3)]))
        await settle()
        assert len(pusher.calls) == 1
        s.on_tick(tick(101, asks=[(930.0, 3)]))
        await settle()
        assert len(pusher.calls) == 2
        # Unknown token and non-matching asks are ignored.
        s.on_tick(tick(999, asks=[(1.0, 1)]))
        s.on_tick(tick(101, asks=[(951.0, 10)]))
        await settle()
        assert len(pusher.calls) == 2
    asyncio.run(run())


def test_bid_target_fires_for_a_holding(monkeypatch):
    async def run():
        s, pusher, recorded = make_session(monkeypatch)
        s.on_tick(tick(202, bids=[(1009.0, 10)]))
        s.on_tick(tick(202, bids=[(1010.0, 10)]))
        await settle()
        assert len(pusher.calls) == 1 and pusher.calls[0]["title"].startswith("Bid ₹1,010.00 × 10 for your HELD")
        assert recorded[0][1] == "bid_opportunity"
    asyncio.run(run())


def test_push_failure_or_missing_topic_still_records_the_hit(monkeypatch):
    async def run():
        s, pusher, recorded = make_session(monkeypatch, FakePusher(fail=True))
        s.on_tick(tick(101, asks=[(940.0, 3)]))
        await settle()
        assert pusher.calls == [] and [r[0] for r in recorded] == ["bell", "ledger"]
        assert recorded[1][4] is None   # no push latency without a push
        s2, pusher2, recorded2 = make_session(monkeypatch)
        s2.topic = None
        s2.on_tick(tick(101, asks=[(940.0, 3)]))
        await settle()
        assert pusher2.calls == [] and len(recorded2) == 2
    asyncio.run(run())


def test_apply_seeds_dedupe_from_todays_ledger(monkeypatch):
    s, pusher, recorded = make_session(monkeypatch)
    s.apply({"thresholds": s.thresholds, "token_map": s.token_map, "bonds": s.bonds,
             "missing": 0, "solved": 0, "topic": "topic-abcdef", "token": "tok",
             "dedupe": {("rule:7", "INE000A"): {"side": "ask", "price": 940, "qty": 3,
                                                 "exchange": "BSE", "ytm": 12.4}}})
    async def run():
        s.on_tick(tick(101, asks=[(940.0, 3)]))   # already alerted today
        await settle()
        assert pusher.calls == []
    asyncio.run(run())


def test_nudge_once_when_rules_exist_but_kite_is_disconnected(monkeypatch):
    s, pusher, _ = make_session(monkeypatch)
    s.access_token = None
    s.nudge_if_disconnected()
    s.nudge_if_disconnected()
    assert len(pusher.calls) == 1 and "Connect Kite" in pusher.calls[0]["title"]
    s2, pusher2, _ = make_session(monkeypatch)
    s2.access_token = "live"
    s2.nudge_if_disconnected()
    assert pusher2.calls == []


# --- connections -------------------------------------------------------------------

class FakeConn:
    instances: list = []

    def __init__(self, user_id, access_token):
        self.user_id, self.access_token = user_id, access_token
        self.task = None
        self.subscribed: set[int] = set()
        self.queue: asyncio.Queue = asyncio.Queue()
        FakeConn.instances.append(self)

    async def run(self):
        await asyncio.sleep(3600)

    async def attach(self, tokens):
        self.subscribed = set(tokens)
        self.queue = asyncio.Queue()
        return self.queue

    async def detach(self, queue):
        self.subscribed = set()


def test_reconcile_splits_tokens_across_sockets_and_reconnects_on_close(monkeypatch):
    FakeConn.instances.clear()
    monkeypatch.setattr(w.kite_ticker, "_UserConn", FakeConn)
    monkeypatch.setattr(w, "TOKENS_PER_CONN", 2)
    monkeypatch.setattr(w, "RECONNECT_MIN", 0.0)

    async def run():
        s, pusher, _ = make_session(monkeypatch)
        s.access_token = "tok"
        s.token_map = {1: ("A", "NSE"), 2: ("A", "BSE"), 3: ("B", "NSE")}
        await s.reconcile_connections()
        assert [sorted(c.subscribed) for c in FakeConn.instances] == [[1, 2], [3]]
        # Simulate Kite closing socket 0: the consumer reconnects and resubscribes.
        FakeConn.instances[0].queue.put_nowait(None)
        await asyncio.sleep(0.05)
        assert len(FakeConn.instances) == 3 and sorted(FakeConn.instances[2].subscribed) == [1, 2]
        # A refresh that drops token 3 closes the second socket.
        s.token_map = {1: ("A", "NSE"), 2: ("A", "BSE")}
        await s.reconcile_connections()
        assert len(s.conns) == 1
        await s.disconnect()
        assert s.conns == [] and s.tasks == []
    asyncio.run(run())


# --- health verdict -----------------------------------------------------------------

def test_watcher_health_only_speaks_in_market_hours():
    at = ist(10, 0).astimezone(UTC)
    fresh = {"beat_at": at - timedelta(seconds=20), "thresholds_ready": True, "push_p99_ms": 300}
    assert health.watcher_problems(fresh, at) == []
    stale = {**fresh, "beat_at": at - timedelta(minutes=9)}
    assert health.watcher_problems(stale, at) == ["watcher: no heartbeat since 09:51 IST"]
    slow = {**fresh, "push_p99_ms": 2600, "thresholds_ready": False}
    assert health.watcher_problems(slow, at) == ["watcher: alert thresholds not fully built",
                                                 "watcher: push p99 2.6s"]
    assert health.watcher_problems(None, at) == ["watcher: never reported a heartbeat"]
    evening = ist(20, 0).astimezone(UTC)
    assert health.watcher_problems(None, evening) == []
    assert health.watcher_problems({**stale, "beat_at": evening - timedelta(hours=5)}, evening) == []


@pytest.mark.parametrize("value,ok", [("2026-10-06T04:01:41+00:00", True), ("junk", False), (None, False)])
def test_quote_timestamp_parsing(value, ok):
    assert (w.quote_ts(value) is not None) is ok
