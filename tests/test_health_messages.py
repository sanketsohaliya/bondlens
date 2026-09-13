from datetime import UTC, datetime

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


def test_both_runs_failing_says_so():
    msg = health.nsdl_problem([run(False, 0, 48, "48 fetch failures (503x192)"),
                               run(False, 0, 48, "48 fetch failures (503x192)")], EVENING)
    assert msg is not None
    assert msg.startswith("nsdl: morning and midday runs FAILED")
    assert "Auto-retry" not in msg
