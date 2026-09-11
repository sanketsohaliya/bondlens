from copy import deepcopy
from datetime import UTC, date, datetime

from backend.analytics.schedule import Flow
from backend.exchange_evidence import build_evidence

ISIN = "INE148I07RZ0"
OTHER = "INE282H07091"
TODAY = date(2026, 9, 11)
DUE = date(2026, 9, 27)


def nse(text, record_id=1, seq="notice-1", **raw):
    return {
        "id": record_id, "raw_id": record_id, "source": "nse_debt_announcements",
        "source_record_id": seq, "issuer_name": "Issuer", "category_raw": "Record Date Updates",
        "headline": text, "announced_at": datetime(2026, 9, 10, 10, tzinfo=UTC),
        "first_seen_at": datetime(2026, 9, 11, tzinfo=UTC),
        "attachment_urls": ["https://nsearchives.nseindia.com/notice.pdf"],
        "raw": {"seq_id": seq, "attchmntText": text, "an_dt": "10-SEP-2026 15:30:00", **raw},
    }


def text(record="12-Sep-2026", due="27-Sep-2026", isin=ISIN, nature="Interest Payment"):
    return f"Issuer filing. Nature of Payment - {nature} , Record date - {record} , Due date - {due} , ISIN - {isin}"


def test_exact_due_match_exposes_only_record_date_difference_without_mutating_flows():
    flows = [Flow("interest", DUE, record_date=date(2026, 9, 12), payment_date=date(2026, 9, 25), amount=90)]
    before = deepcopy(flows)
    view = build_evidence(ISIN, flows, [nse(text(record="11-Sep-2026"))], as_of=TODAY)
    comparison = view["flows"][0]["sources"][0]
    fields = {f["field"]: f for f in comparison["fields"]}
    assert fields["due_date"]["state"] == "agree"
    assert fields["record_date"]["state"] == "different"
    assert fields["payment_date"]["state"] == "not_supplied"
    assert fields["amount"]["state"] == "not_supplied"
    assert flows == before


def test_introductory_isin_does_not_inherit_another_bonds_event():
    body = f"Issuer discusses {ISIN} and {OTHER}. " + text(isin=ISIN)
    view = build_evidence(OTHER, [Flow("interest", DUE, record_date=date(2026, 9, 12))],
                          [nse(body)], as_of=TODAY)
    assert view["flows"][0]["sources"] == []
    assert view["notices"][0]["status"] == "context_only"
    assert view["notices"][0]["event_isin"] == ISIN


def test_timing_only_representations_collapse_without_losing_provenance():
    rows = [nse(text(), record_id=i, difference=str(i), exchdisstime=str(i)) for i in range(1, 100)]
    view = build_evidence(ISIN, [Flow("interest", DUE, record_date=date(2026, 9, 12))], rows, as_of=TODAY)
    assert len(view["notices"]) == 1
    assert view["notices"][0]["representation_count"] == 99
    assert len(view["notices"][0]["raw_ids"]) == 99
    assert len(view["flows"][0]["sources"][0]["notice_ids"]) == 1


def bse(record="12 Sep 2026", purpose="Payment of Interest ", **raw):
    return {"id": 100, "raw_id": 100, "source": "bse_corporate_actions", "verified_bse": True,
            "scrip_code": "977935", "category_raw": purpose, "headline": purpose,
            "raw": {"scrip_code": 977935, "Purpose": purpose, "RD_Date": record, "Ex_date": record, **raw}}


def test_bse_exact_record_date_is_only_limited_record_date_evidence():
    view = build_evidence(ISIN, [Flow("interest", DUE, record_date=date(2026, 9, 12), amount=90)],
                          [bse()], as_of=TODAY)
    source = view["flows"][0]["sources"][0]
    assert source["anchor"] == "record_date"
    states = {f["field"]: f["state"] for f in source["fields"]}
    assert states == {"due_date": "not_supplied", "record_date": "agree", "payment_date": "not_supplied", "amount": "not_supplied"}
    assert view["notices"][0]["due_date"] is None
    assert view["notices"][0]["ex_date"] == date(2026, 9, 12)


def test_monedo_nearby_record_date_is_unmatched_not_an_invented_payment():
    flow = Flow("interest", date(2026, 10, 1), record_date=date(2026, 9, 15), amount=118.36)
    view = build_evidence(ISIN, [flow], [bse(record="16 Sep 2026")], as_of=TODAY)
    assert view["flows"][0]["sources"] == []
    assert view["notices"][0]["status"] == "unmatched"
    assert view["notices"][0]["due_date"] is None


def test_bse_unverified_identity_and_conflicting_purpose_are_review_only():
    flow = Flow("redemption", DUE, record_date=date(2026, 9, 12))
    for record in [bse(purpose="Redemption of CP"), bse(purpose="Buy Back of Shares"),
                   {**bse(purpose="Redemption of NCD"), "verified_bse": False}]:
        view = build_evidence(ISIN, [flow], [record], as_of=TODAY)
        assert view["notices"][0]["status"] == "review_only"
        assert view["flows"][0]["sources"] == []


def test_early_redemption_remains_a_dated_review_notice():
    view = build_evidence(ISIN, [Flow("redemption", DUE, record_date=date(2026, 9, 12))],
                          [bse(purpose="Early Redemption of NCD")], as_of=TODAY)
    notice = view["notices"][0]
    assert notice["redemption_form"] == "early"
    assert notice["status"] == "review_only"
    assert notice["due_date"] is None


def test_combined_interest_and_partial_principal_do_not_add_flows_or_money():
    flows = [Flow("interest", DUE, record_date=date(2026, 9, 12), amount=90),
             Flow("redemption", DUE, record_date=date(2026, 9, 12), amount=2000)]
    view = build_evidence(ISIN, flows, [nse(text(nature="Interest and Part Principal Payment"))], as_of=TODAY)
    assert len(view["flows"]) == 2
    assert view["notices"][0]["events"] == ["interest", "redemption"]
    assert all(row["sources"] for row in view["flows"])
    assert [f.amount for f in flows] == [90, 2000]


def test_competing_versions_are_not_resolved_by_ingestion_order():
    flow = Flow("interest", DUE, record_date=date(2026, 9, 12))
    rows = [nse(text(), 1), nse(text(record="11-Sep-2026"), 2)]
    first = build_evidence(ISIN, [flow], rows, as_of=TODAY)
    reverse = build_evidence(ISIN, [flow], list(reversed(rows)), as_of=TODAY)
    assert first == reverse
    assert first["flows"][0]["sources"][0]["state"] == "conflicting"
    field = next(f for f in first["flows"][0]["sources"][0]["fields"] if f["field"] == "record_date")
    assert field["state"] == "conflicting"


def test_same_notice_id_with_changed_due_date_is_competing_not_silently_latest():
    flows = [Flow("interest", DUE, record_date=date(2026, 9, 12)),
             Flow("interest", date(2026, 9, 28), record_date=date(2026, 9, 12))]
    view = build_evidence(ISIN, flows, [nse(text(), 1), nse(text(due="28-Sep-2026"), 2)], as_of=TODAY)
    assert all(s["state"] == "conflicting" for f in view["flows"] for s in f["sources"])
    assert view["summary"]["differences"] == 2


def test_competing_version_outside_window_remains_inspectable_without_counting_extra_flows():
    flows = [Flow("interest", DUE, record_date=date(2026, 9, 12)),
             Flow("interest", date(2030, 9, 27), record_date=date(2030, 9, 12))]
    rows = [nse(text(), 1), nse(text(due="27-Sep-2030", record="12-Sep-2030"), 2)]
    view = build_evidence(ISIN, flows, rows, as_of=TODAY)
    assert len(view["notices"]) == 2
    assert view["summary"]["checked_flows"] == 1
    assert view["summary"]["matched_flows"] == 1
    other = next(n for n in view["notices"] if n["due_date"].year == 2030)
    assert "related_version_outside_window" in other["reasons"]


def test_missing_redemption_component_is_not_hidden_by_matched_interest():
    view = build_evidence(ISIN, [Flow("interest", DUE, record_date=date(2026, 9, 12))],
                          [nse(text(nature="Interest Payment and Redemption"))], as_of=TODAY)
    assert view["notices"][0]["unmatched_events"] == ["redemption"]
    assert view["unmatched_notice_ids"]
    assert view["flows"][0]["sources"]


def test_passed_record_date_does_not_hide_upcoming_flow_evidence():
    flow = Flow("interest", DUE, record_date=date(2026, 9, 10))
    assert not flow.receivable_by_buyer(TODAY)
    view = build_evidence(ISIN, [flow], [nse(text(record="10-Sep-2026"))], as_of=TODAY)
    assert view["flows"][0]["checked"]
    assert view["flows"][0]["sources"]


def test_matches_full_schedule_before_filtering_payment_date_window():
    flow = Flow("interest", date(2026, 9, 12), payment_date=date(2026, 9, 10))
    view = build_evidence(ISIN, [flow], [nse(text(due="12-Sep-2026", record="01-Sep-2026"))], as_of=TODAY)
    assert view["notices"][0]["status"] == "matched"
    assert not view["unmatched_notice_ids"]


def test_duplicate_nsdl_candidates_are_ambiguous_without_amount_guessing():
    flows = [Flow("interest", DUE, record_date=date(2026, 9, 12), amount=10),
             Flow("interest", DUE, record_date=date(2026, 9, 12), amount=20)]
    view = build_evidence(ISIN, flows, [nse(text())], as_of=TODAY)
    assert view["flows"][0]["ambiguous"]
    assert view["notices"][0]["status"] == "ambiguous"
    assert not view["flows"][0]["sources"]


def test_partial_input_does_not_make_negative_matching_claims():
    view = build_evidence(ISIN, [Flow("interest", DUE)], [nse(text())], as_of=TODAY, partial=True)
    assert view["partial"]
    assert view["notices"][0]["status"] == "review_only"
    assert "partial_evidence_set" in view["notices"][0]["reasons"]


def test_blocked_source_is_not_used_for_automatic_comparison():
    view = build_evidence(ISIN, [Flow("interest", DUE, record_date=date(2026, 9, 12))], [bse()],
                          as_of=TODAY, availability=[{"source": "BSE", "status": "mapping_blocked"}])
    assert not view["flows"][0]["sources"]
    assert view["notices"][0]["status"] == "review_only"


def test_unknown_and_sentinel_dates_are_review_only():
    for body in [text(record="31-Feb-2026"), text(due="31-Dec-9999"), text(nature="Something new")]:
        view = build_evidence(ISIN, [Flow("interest", DUE)], [nse(body)], as_of=TODAY)
        assert not view["flows"][0]["sources"]
        assert view["notices"][0]["status"] == "review_only"


def test_different_attachments_are_preserved_in_one_semantically_equal_filing():
    a = nse(text(), 1)
    b = nse(text(), 2)
    b["attachment_urls"] = ["https://nsearchives.nseindia.com/second.pdf", "javascript:alert(1)"]
    view = build_evidence(ISIN, [Flow("interest", DUE)], [a, b], as_of=TODAY)
    assert len(view["notices"]) == 1
    assert len(view["notices"][0]["links"]) == 2
    assert not any(link.startswith("javascript") for link in view["notices"][0]["links"])


def test_availability_distinguishes_source_silence_blocking_and_staleness():
    from datetime import timedelta

    from backend.exchange_evidence import source_availability

    now = datetime(2026, 9, 11, 10, tzinfo=UTC)
    complete = {"active": True, "pending": [], "recent_pending": [], "history_from": date(1990, 1, 1)}
    rows = [
        {"source": "nse_debt_announcements", "last_success_at": now, "latest_ok": True, "target": complete},
        {"source": "bse_corporate_actions", "last_success_at": now, "latest_ok": True,
         "target": {**complete, "blocked_reason": "no_verified_scrip"}},
    ]
    result = source_availability(rows, now)
    assert result[0]["status"] == "fresh"
    assert result[1]["status"] == "mapping_blocked"
    rows[0]["last_success_at"] = now - timedelta(hours=20)
    assert source_availability(rows, now)[0]["status"] == "stale"
    rows[0]["target"] = None
    assert source_availability(rows, now)[0]["status"] == "not_captured"
    rows[0]["target"] = {**complete, "pending": [{"from": "1990-01-01", "to": "2020-01-01"}]}
    rows[0]["last_success_at"] = now
    assert source_availability(rows, now)[0]["status"] == "incomplete"


def test_missing_event_isin_cannot_be_borrowed_from_trailing_context():
    body = text(isin="Not supplied") + f" , Related bond mentioned: {ISIN}"
    view = build_evidence(ISIN, [Flow("interest", DUE, record_date=date(2026, 9, 12))], [nse(body)], as_of=TODAY)
    assert not view["flows"][0]["sources"]
    assert view["notices"][0]["event_isin"] is None


def test_valid_event_isin_is_not_contaminated_by_trailing_context():
    body = text() + f" , Related bond mentioned: {OTHER}"
    view = build_evidence(ISIN, [Flow("interest", DUE, record_date=date(2026, 9, 12))], [nse(body)], as_of=TODAY)
    assert view["flows"][0]["sources"]
    assert view["notices"][0]["event_isin"] == ISIN


def test_changed_event_components_under_one_source_id_are_competing_versions():
    flows = [Flow("interest", DUE, record_date=date(2026, 9, 12)),
             Flow("redemption", DUE, record_date=date(2026, 9, 12))]
    rows = [nse(text(), 1), nse(text(nature="Redemption"), 2)]
    view = build_evidence(ISIN, flows, rows, as_of=TODAY)
    assert all(s["state"] == "conflicting" for f in view["flows"] for s in f["sources"])


def test_partial_principal_change_under_one_source_id_requires_review():
    rows = [nse(text(nature="Redemption"), 1), nse(text(nature="Part Principal Payment"), 2)]
    view = build_evidence(ISIN, [Flow("redemption", DUE, record_date=date(2026, 9, 12))], rows, as_of=TODAY)
    assert view["flows"][0]["sources"][0]["state"] == "conflicting"


def test_native_iso_midnight_bse_ex_date_preserves_record_date_only_match():
    view = build_evidence(ISIN, [Flow("interest", DUE, record_date=date(2026, 9, 12))],
                          [bse(Ex_date="2026-09-12T00:00:00")], as_of=TODAY)
    assert view["flows"][0]["sources"][0]["anchor"] == "record_date"
    assert view["notices"][0]["ex_date"] == date(2026, 9, 12)


def test_minimum_calendar_window_does_not_overflow_bse_lookback():
    view = build_evidence(ISIN, [Flow("interest", DUE)], [bse()], as_of=TODAY,
                          date_from=date.min, date_to=date(1, 1, 2))
    assert view["date_from"] == date.min
    assert view["notices"] == []


def test_response_validates_against_the_public_contract():
    from backend.models import CashflowEvidenceResponse

    view = build_evidence(ISIN, [Flow("interest", DUE)], [nse(text()), bse()], as_of=TODAY)
    response = CashflowEvidenceResponse.model_validate(view)
    assert response.isin == ISIN
    assert response.summary.checked_flows == 1
