from datetime import UTC, datetime, timedelta

from backend import quote_time

NOW = datetime(2026, 9, 15, 5, tzinfo=UTC)  # 10:30 IST


def codes(meta) -> set[str]:
    return {w["code"] for w in meta["warnings"]}


def test_an_unchanged_book_is_a_live_snapshot_not_an_expired_one():
    # Kite's timestamp is when the book last changed; a resting ask from
    # the open still carries 09:15 at 10:30. Disclose, do not expire.
    open_ = NOW - timedelta(hours=1, minutes=15)
    meta = quote_time.metadata(NOW, open_, now=NOW)
    assert meta["fetched_at"] == NOW.isoformat() and meta["quote_at"] == open_.isoformat()
    assert "expires_at" not in meta
    assert codes(meta) == {"book_unchanged"}
    [note] = meta["warnings"]
    assert note["severity"] == "info" and "09:15 IST" in note["message"]


def test_a_book_that_just_changed_carries_no_note():
    assert quote_time.metadata(NOW, NOW - timedelta(seconds=20), now=NOW)["warnings"] == []


def test_missing_timestamp_is_disclosed_and_future_timestamp_rejected():
    assert codes(quote_time.metadata(NOW, now=NOW)) == {"quote_time_unknown"}
    assert codes(quote_time.metadata(NOW, NOW + timedelta(hours=1), now=NOW)) == {"future_quote_time"}


def test_missing_or_future_capture_time_is_flagged():
    assert "invalid_fetch_time" in codes(quote_time.metadata(None, NOW, now=NOW))
    assert "invalid_fetch_time" in codes(quote_time.metadata(NOW + timedelta(minutes=5), NOW, now=NOW))


def test_source_time_formats_are_normalised_to_utc():
    assert quote_time.timestamp("2026-09-15 10:30:00") == NOW
    assert quote_time.timestamp("15-Sep-2026 10:30:00 IST") == NOW
    assert quote_time.timestamp("junk") is None
