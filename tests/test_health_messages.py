from datetime import UTC, date, datetime, timedelta

from backend import health

# 08:45 IST on 13 Sept 2026 = 03:15 UTC
MORNING = datetime(2026, 9, 13, 3, 15, tzinfo=UTC)
EVENING = datetime(2026, 9, 13, 14, 15, tzinfo=UTC)  # 19:45 IST


def run(ok, fetched=0, failed=0, notes=None):
    return {"ok": ok, "fetched": fetched, "failed": failed, "notes": notes, "at": None}


def test_no_run_today_is_still_reported():
    assert health.nsdl_problem([], MORNING) == "nsdl: no run today"


def test_a_healthy_latest_run_is_silent():
    assert health.nsdl_problem([run(True, 2852, 16, "16 fetch failures")], MORNING) is None
    # A failed morning followed by a good midday retry is also silent.
    assert health.nsdl_problem([run(False, 0, 48, "48 fetch failures"),
                                run(True, 2, 16, "16 fetch failures")], EVENING) is None


def test_the_13_sept_outage_reads_as_actionable_in_the_morning():
    msg = health.nsdl_problem([run(False, 0, 48, "48 fetch failures (503x192)")], MORNING)
    assert msg == ("nsdl: today's run FAILED \u2014 48 fetch failures (503x192). "
                   "Fetched nothing; NSDL upstream likely down. Auto-retry at 13:00 IST.")


def test_a_fail_fast_abort_is_named_and_no_retry_promised_after_midday():
    msg = health.nsdl_problem(
        [run(False, 0, 0, "aborted: NsdlOutage: 12 consecutive upstream failures (http=503)")],
        EVENING)
    assert msg is not None
    assert "NSDL upstream down (aborted early)" in msg
    assert "Auto-retry" not in msg


def test_a_passed_canary_is_not_called_an_outage():
    # 15 Sept 2026 read "Fetched nothing; NSDL upstream likely down" while
    # NSDL was fine - only the 16 chronic per-record 503s had been due.
    msg = health.nsdl_problem(
        [run(False, 0, 15, "15 fetch failures (503x26); canary ok - per-record NSDL errors, not an outage")],
        MORNING)
    assert msg is not None
    assert "per-record, not an outage" in msg
    assert "likely down" not in msg


def test_both_runs_failing_says_so():
    msg = health.nsdl_problem([run(False, 0, 48, "48 fetch failures (503x192)"),
                               run(False, 0, 48, "48 fetch failures (503x192)")], EVENING)
    assert msg is not None
    assert msg.startswith("nsdl: morning and midday runs FAILED")
    assert "Auto-retry" not in msg


# --- EOD expected date ---------------------------------------------------------

TUE_15_SEP = date(2026, 9, 15)


def test_a_plain_weekday_expects_yesterday():
    assert health.expected_trade_date(TUE_15_SEP, set()) == (date(2026, 9, 14), [])


def test_monday_expects_friday():
    assert health.expected_trade_date(date(2026, 9, 14), set()) == (date(2026, 9, 11), [])


def test_an_inferred_holiday_walks_back_to_the_last_trading_day():
    # 14 Sept 2026 (Ganesh Chaturthi): all three exchanges answered 404 to
    # the 19:00 harvest, yet the check demanded trades for it.
    holidays = {date(2026, 9, 14)}
    assert health.expected_trade_date(TUE_15_SEP, holidays) == (
        date(2026, 9, 11), [date(2026, 9, 14)])


def test_a_holiday_cluster_around_a_weekend_is_still_a_holiday():
    # e.g. Thu+Fri closed, then the weekend -> Monday expects Wednesday.
    holidays = {date(2026, 9, 10), date(2026, 9, 11)}
    assert health.expected_trade_date(date(2026, 9, 14), holidays) == (
        date(2026, 9, 9), [date(2026, 9, 11), date(2026, 9, 10)])


def test_too_many_file_less_weekdays_is_a_publication_failure_not_a_holiday():
    start = date(2026, 9, 7)  # Mon
    holidays = {start + timedelta(days=i) for i in range(5)}  # Mon-Fri all "absent"
    expected, skipped = health.expected_trade_date(date(2026, 9, 14), holidays)
    assert len(skipped) == health.MAX_INFERRED_HOLIDAYS
    assert expected == date(2026, 9, 8)  # still demands data -> alert fires
