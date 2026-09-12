from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .analytics.valuation import warning

MAX_AGE_SECONDS = 90
IST = ZoneInfo("Asia/Kolkata")


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
    expires = now
    if fetched is None or fetched > now + timedelta(seconds=30):
        warnings.append(warning("invalid_fetch_time", "Quote capture time is missing or future-dated; refresh the quote"))
    else:
        expires = min(fetched + timedelta(seconds=MAX_AGE_SECONDS),
                      datetime.combine(fetched.astimezone(IST).date() + timedelta(days=1), time(), IST).astimezone(UTC))
    if quote is None:
        warnings.append(warning("quote_time_unknown", "Source quote timestamp is unavailable; capture time is not proof of an executable offer", "info"))
    elif quote > now + timedelta(seconds=30):
        warnings.append(warning("future_quote_time", "Source quote timestamp is in the future; refresh before using it"))
        expires = now
    else:
        expires = min(expires, quote + timedelta(seconds=MAX_AGE_SECONDS),
                      datetime.combine(quote.astimezone(IST).date() + timedelta(days=1), time(), IST).astimezone(UTC))
    if expires <= now:
        warnings.append(warning("stale_quote", "Quote snapshot has expired; refresh before treating this as a current ask"))
    return {"fetched_at": fetched.isoformat() if fetched else None,
            "quote_at": quote.isoformat() if quote else None,
            "expires_at": expires.isoformat(), "warnings": warnings}
