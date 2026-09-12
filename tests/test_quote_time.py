from datetime import UTC, datetime, timedelta

from backend import quote_time

NOW = datetime(2026, 9, 12, 10, tzinfo=UTC)


def test_capture_time_does_not_replace_source_time():
    old = NOW - timedelta(minutes=5)
    metadata = quote_time.metadata(NOW, old, now=NOW)
    assert metadata["quote_at"] == old.isoformat()
    assert "stale_quote" in {w["code"] for w in metadata["warnings"]}


def test_missing_timestamp_is_disclosed_and_future_timestamp_rejected():
    assert quote_time.metadata(NOW, now=NOW)["warnings"][0]["code"] == "quote_time_unknown"
    future = quote_time.metadata(NOW, NOW + timedelta(hours=1), now=NOW)
    assert future["expires_at"] == NOW.isoformat()
    assert "future_quote_time" in {w["code"] for w in future["warnings"]}


def test_a_new_tick_elsewhere_does_not_refresh_an_old_side():
    first = quote_time.metadata(NOW, NOW, now=NOW)
    later = quote_time.metadata(first["fetched_at"], first["quote_at"], now=NOW + timedelta(seconds=95))
    assert later["expires_at"] == first["expires_at"]
    assert "stale_quote" in {w["code"] for w in later["warnings"]}


def test_expiry_stops_at_ist_midnight():
    capture = datetime(2026, 9, 12, 18, 29, 50, tzinfo=UTC)
    result = quote_time.metadata(capture, capture, now=capture)
    assert result["expires_at"] == "2026-09-12T18:30:00+00:00"
