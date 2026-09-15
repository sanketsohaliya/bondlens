"""Capture metadata for depth snapshots.

A snapshot is what we saw when we fetched it - nothing here expires it.
Two times travel with each side of the book:

  fetched_at  when BondLens pulled the depth from Kite/the exchange
  quote_at    Kite's ``timestamp``: when the order book last *changed*
              at the exchange, NOT when it was observed. For an illiquid
              bond whose ask has rested untouched since the open this is
              hours old while the depth is perfectly current. (15 Sept
              2026: of 202 listings with a resting ask, 12 had changed in
              the last 90 seconds - a 90s expiry keyed on this field had
              been hiding the other 190.)

So quote_at is disclosed, and when it lags the capture by more than a
minute the book is described as unchanged since then; only a future-
dated timestamp is treated as bad data.
"""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .analytics.valuation import warning

IST = ZoneInfo("Asia/Kolkata")
UNCHANGED_NOTE_AFTER = timedelta(seconds=60)


def timestamp(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and ":" in value:
        text = value.strip().removesuffix(" IST").replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%d-%b-%Y %H:%M:%S", "%d %b %Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    else:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)).astimezone(UTC)


def metadata(fetched_at, quote_at=None, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    fetched = timestamp(fetched_at)
    quote = timestamp(quote_at)
    warnings = []
    if fetched is None or fetched > now + timedelta(seconds=30):
        warnings.append(warning("invalid_fetch_time", "Quote capture time is missing or future-dated; refresh the quote"))
    if quote is None:
        warnings.append(warning("quote_time_unknown", "Source quote timestamp is unavailable; capture time is not proof of an executable offer", "info"))
    elif quote > now + timedelta(seconds=30):
        warnings.append(warning("future_quote_time", "Source quote timestamp is in the future; refresh before using it"))
    elif fetched is not None and fetched - quote > UNCHANGED_NOTE_AFTER:
        since = quote.astimezone(IST).strftime("%H:%M")
        warnings.append(warning("book_unchanged", f"Order book last changed {since} IST; resting orders may be old", "info"))
    return {"fetched_at": fetched.isoformat() if fetched else None,
            "quote_at": quote.isoformat() if quote else None,
            "warnings": warnings}
