"""ntfy pushes: ops health (deployment topic) and per-user opportunity
alerts (each user's own topic).

ntfy's model is public-topic: the topic name is the secret, so topics
are never logged. The watcher wants the push on the wire as fast as
possible, so Pusher keeps one HTTP session (TLS kept alive, warmed with
a HEAD at start of day) and the async wrapper only hops to a thread.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import requests

NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh")
OPS_TOPIC = os.environ.get("NTFY_TOPIC", "")

PRIORITY_URGENT = "5"   # breaks through Do Not Disturb (opportunities, by choice)
PRIORITY_HIGH = "high"  # ops health


def _headers(title: str, priority: str, tags: str, click: str | None) -> dict[str, str]:
    # ntfy headers must be latin-1; titles/bodies carry rupee signs and
    # dashes, so non-ASCII header values are RFC 2047-encoded (ntfy decodes).
    h = {"Title": _header_value(title), "Priority": priority, "Tags": tags}
    if click:
        h["Click"] = click
    return h


def _header_value(text: str) -> str:
    try:
        text.encode("latin-1")
        return text
    except UnicodeEncodeError:
        import base64
        return "=?UTF-8?B?" + base64.b64encode(text.encode()).decode() + "?="


class Pusher:
    """One keep-alive session; push() returns the round-trip in ms."""

    def __init__(self, base_url: str = NTFY_URL, session: Any = None, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    def warm(self, topic: str) -> None:
        """Open the TLS connection ahead of the first push (best effort)."""
        try:
            self.session.head(f"{self.base_url}/{topic}", timeout=self.timeout)
        except Exception:  # noqa: BLE001
            pass

    def push(self, topic: str, title: str, body: str, *, priority: str = PRIORITY_HIGH,
             tags: str = "bondlens", click: str | None = None) -> int:
        t0 = time.perf_counter()
        resp = self.session.post(f"{self.base_url}/{topic}", data=body.encode(),
                                 headers=_headers(title, priority, tags, click),
                                 timeout=self.timeout)
        resp.raise_for_status()
        return int((time.perf_counter() - t0) * 1000)

    async def push_async(self, topic: str, title: str, body: str, **kw: Any) -> int:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.push(topic, title, body, **kw))


_default = Pusher()


def push(topic: str, title: str, body: str, **kw: Any) -> int:
    return _default.push(topic, title, body, **kw)


def notify_ops(title: str, message: str, priority: str = PRIORITY_HIGH) -> bool:
    """Deployment-level health alert; False (with a printed note) when unset."""
    if not OPS_TOPIC:
        print(f"NTFY_TOPIC unset — would have sent: [{title}] {message}")
        return False
    _default.push(OPS_TOPIC, title, message, priority=priority, tags="warning,bondlens")
    return True
