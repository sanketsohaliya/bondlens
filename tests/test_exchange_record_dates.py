"""The exchange's record date governs eligibility when it differs from
NSDL's (INE532F07EM7: BSE 09 Oct 2026, NSDL 11 Oct 2026; a lot bought on
09 Oct does not receive the 26 Oct coupon)."""
from datetime import UTC, date, datetime

from backend import exchange_evidence, schedules
from backend.analytics import schedule, valuation

TODAY = date(2026, 10, 10)
TERMS = {"face_value": 1000, "coupon_rate": 10.10, "coupon_type": "Simple",
         "interest_payment_frequency": "Once a year", "day_count_convention": "Actual/actual",
         "allotment_date": date(2023, 10, 26), "redemption_date": date(2028, 10, 26)}


def rows():
    out = []
    for y in (2024, 2025, 2026, 2027, 2028):
        out.append({"event": "Interest", "due_date": date(y, 10, 26), "record_date": date(y, 10, 11),
                    "payment_date": date(y, 10, 26), "amount_payable": 101, "amount_redeem": None,
                    "amount_outstanding": None})
    out.append({"event": "Full Redemption", "due_date": date(2028, 10, 26), "record_date": date(2028, 10, 11),
                "payment_date": date(2028, 10, 26), "amount_payable": 1000, "amount_redeem": None,
                "amount_outstanding": None})
    return out


def bse_notice(record="09 Oct 2026", *, seen="2026-09-11", verified=True, rid="7001"):
    at = datetime.fromisoformat(seen).replace(tzinfo=UTC)
    return {"id": 1, "raw_id": 1, "source": "bse_corporate_actions", "source_record_id": rid,
            "scrip_code": "974001", "issuer_name": "EDELWEISS", "category_raw": "Payment of Interest",
            "headline": "Payment of Interest", "announced_at": at, "first_seen_at": at, "last_seen_at": at,
            "attachment_urls": [], "source_url": None, "verified_bse": verified,
            "raw": {"Purpose": "Payment of Interest", "RD_Date": record, "Ex_date": record, "scrip_code": "974001"}}


def test_notice_within_window_overrides_the_record_date_and_eligibility():
    sched = schedules.build("INE532F07EM7", rows(), TERMS, TODAY, [bse_notice()])
    [flow] = [f for f in sched.flows if f.due_date == date(2026, 10, 26) and f.event == "interest"]
    assert flow.record_date == date(2026, 10, 9)
    [adj] = flow.adjustments
    assert adj["kind"] == "record_date_from_exchange" and adj["source"] == "BSE"
    assert adj["previous_record_date"] == "2026-10-11"
    assert adj["reason"] == "Record date 09 Oct 2026 per BSE notice (NSDL lists 11 Oct 2026); eligibility follows the exchange"
    # Bought on the exchange record date: settles too late.
    assert flow.receivable_by_holder(date(2026, 10, 9)) is False
    assert flow.receivable_by_holder(date(2026, 10, 8)) is True
    # A buyer today (10 Oct) no longer counts the coupon in the yield.
    assert flow.receivable_by_buyer(TODAY) is False
    plain = schedule.build("INE532F07EM7", rows(), TERMS, today=TODAY)
    [nsdl] = [f for f in plain.flows if f.due_date == date(2026, 10, 26) and f.event == "interest"]
    assert nsdl.receivable_by_buyer(TODAY) is True
    v = valuation.calculate(1010.0, sched, TERMS, TODAY)
    [note] = [w for w in v["warnings"] if w["code"] == "record_date_exchange"]
    assert note["severity"] == "info" and v["ytm_reliable"] is True
    assert note["message"] == ("Record date for the 26 Oct 2026 interest is 09 Oct 2026 per BSE notice "
                               "(NSDL lists 11 Oct 2026); eligibility follows the exchange.")
    assert valuation.calculate(1010.0, plain, TERMS, TODAY)["ytm"] > v["ytm"]


def test_no_override_outside_the_window_or_for_review_only_notices():
    far = schedules.build("X", rows(), TERMS, TODAY, [bse_notice("20 Sep 2026")])
    assert not any(a["kind"] == "record_date_from_exchange" for f in far.flows for a in f.adjustments)
    unverified = schedules.build("X", rows(), TERMS, TODAY, [bse_notice(verified=False)])
    assert not any(a["kind"] == "record_date_from_exchange" for f in unverified.flows for a in f.adjustments)
    agree = schedules.build("X", rows(), TERMS, TODAY, [bse_notice("11 Oct 2026")])
    assert not any(a["kind"] == "record_date_from_exchange" for f in agree.flows for a in f.adjustments)


def test_latest_capture_wins_and_evidence_marks_the_difference():
    older = bse_notice("08 Oct 2026", seen="2026-09-01", rid="7000")
    newer = bse_notice("09 Oct 2026", seen="2026-10-01", rid="7001")
    sched = schedule.build("INE532F07EM7", rows(), TERMS, today=TODAY)
    over = exchange_evidence.record_date_overrides("INE532F07EM7", sched.flows, [older, newer])
    assert [o["record_date"] for o in over.values()] == [date(2026, 10, 9)]
    ev = exchange_evidence.build_evidence("INE532F07EM7", sched.flows, [newer], as_of=TODAY)
    [row] = [r for r in ev["flows"] if r["due_date"] == date(2026, 10, 26) and r["event"] == "interest"]
    [src] = row["sources"]
    assert src["source"] == "BSE" and any(f["field"] == "record_date" and f["state"] == "different" for f in src["fields"])
    assert ev["unmatched_notice_ids"] == []
