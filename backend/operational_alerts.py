import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from psycopg import Error
from psycopg_pool import PoolTimeout

from . import db


def report(name: str, problems: list[str], sender: Callable, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    fingerprint = hashlib.sha256("\n".join(sorted(problems)).encode()).hexdigest()
    active = bool(problems)
    sent = False
    try:
        with db._conn() as conn:
            conn.execute("INSERT INTO operational_alert_state (name) VALUES (%s) ON CONFLICT DO NOTHING", (name,))
            previous = conn.execute("SELECT * FROM operational_alert_state WHERE name = %s FOR UPDATE", (name,)).fetchone()
            assert previous is not None
            last_notice = previous["last_notified_at"]
            due = active and (not previous["active"] or previous["fingerprint"] != fingerprint
                              or last_notice is None or now - last_notice >= timedelta(hours=24))
            recovered = not active and previous["active"] and last_notice is not None
            if due or recovered:
                title = f"BondLens {name}: {len(problems)} issue(s)" if active else f"BondLens {name}: recovered"
                body = "\n".join(problems) if active else "The latest check succeeded; the previously reported issue is no longer observed."
                sent = bool(sender(title, body, priority="high" if active else "default"))
            conn.execute("UPDATE operational_alert_state SET active = %s, fingerprint = %s, "
                         "last_notified_at = %s WHERE name = %s",
                         (active if sent or not recovered else previous["active"], fingerprint,
                          now if sent else None if due else last_notice, name))
        return sent
    except (Error, PoolTimeout):
        if sent or not active:
            return sent
        return bool(sender(f"BondLens {name}: issue", "\n".join(problems) + "\nAlert deduplication is unavailable; this notice may repeat.", priority="high"))
