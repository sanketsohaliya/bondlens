import json
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest
import requests
from requests.structures import CaseInsensitiveDict

from backend import corporate_action_sources as sources

NSE = "nse_debt_announcements"
BSE = "bse_corporate_actions"
BOND = "INE001A07018"
OTHER_BOND = "INE002A08019"
EQUITY = "INE001A01018"
CP = "INE001A14018"
DAY = date(2026, 9, 10)


def request(source=NSE, start=DAY, end=DAY):
    return sources.Request(source, start, end)


def nse_row(**fields):
    return {"an_dt": "10-Sep-2026 12:30:00", "sm_isin": BOND, **fields}


def bse_row(**fields):
    return {"exdate": "10 Sep 2026", "scrip_code": 977935, **fields}


def encoded(value):
    return json.dumps(value).encode()


def test_requests_are_frozen_and_use_the_native_date_axes():
    assert sources.SOURCES == (NSE, BSE)
    nse = request()
    assert nse.url == "https://www.nseindia.com/api/corporate-announcements"
    assert nse.params == {
        "index": "debt", "from_date": "10-09-2026", "to_date": "10-09-2026"
    }
    assert nse.axis == "an_dt"
    bse = request(BSE)
    assert bse.url == "https://api.bseindia.com/BseIndiaAPI/api/DefaultData/w"
    assert bse.params == {
        "Fdate": "20260910", "TDate": "20260910", "Purposecode": "",
        "ddlcategorys": "E", "ddlindustrys": "", "scripcode": "",
        "strSearch": "S", "segment": "Debt",
    }
    assert bse.axis == "exdate"
    with pytest.raises(FrozenInstanceError):
        cast(Any, nse).source = BSE
    with pytest.raises(ValueError):
        request("unknown")
    with pytest.raises(ValueError):
        request(start=date(2026, 9, 11))


def test_decoder_preserves_exact_native_spans_order_duplicates_and_precision():
    first = '{ "sm_isin":"INE001A07018", "an_dt":"10-Sep-2026 00:00:00", "amount":0.12345678901234567890123456789, "unknown":{"values":[null,"a}\\\"b",1e-009]} }'
    second = '{"sm_isin":null,"an_dt":"10-Sep-2026 23:59:59","desc":"Unknown future category","extra":"\\u20b9"}'
    records = sources.decode(request(), f' \n[ {first},\n{second}, {first} ]\t'.encode())
    assert [record.position for record in records] == [0, 1, 2]
    assert [record.text for record in records] == [first, second, first]
    assert records[0].data["unknown"] == {"values": [None, 'a}"b', 1e-9]}
    assert records[1].data["sm_isin"] is None
    assert "unknown" not in records[1].data
    assert records[1].data["extra"] == "₹"


@pytest.mark.parametrize("source", sources.SOURCES)
@pytest.mark.parametrize("body", [b"[]", b'{"data":[]}', b'{"data": [], "total": 0}'])
def test_known_empty_responses(source, body):
    assert sources.decode(request(source), body) == []


@pytest.mark.parametrize("body", [
    b"", b"<html>private challenge</html>", b'"No data"', b'"error"',
    b"null", b"true", b"{}", b'{"error":"private details"}',
    b'{"data":[],"error":"private details"}',
    b'{"data":[],"message":"private details"}', b'{"data":{}}',
    b"[1]", b"[{}]", b"[[]]", b"[] trailing", b"[,]", b"[{},]", b"\xff",
    b'{"data":[], "data":[]}',
    b'[{"an_dt":"10-Sep-2026 12:30:00","sm_isin":null,"x":NaN}]',
    b'[{"an_dt":"10-Sep-2026 12:30:00","sm_isin":null,"x":{"a":1,"a":2}}]',
])
def test_invalid_responses_have_only_safe_errors(body):
    with pytest.raises(sources.InvalidResponse) as raised:
        sources.decode(request(), body)
    assert raised.value.code in {"invalid_json", "invalid_schema", "missing_date"}
    assert str(raised.value) == raised.value.code
    assert "private" not in str(raised.value)


@pytest.mark.parametrize("source, row", [(NSE, nse_row()), (BSE, bse_row())])
def test_data_envelope_keeps_original_native_span(source, row):
    text = json.dumps(row, indent=2)
    records = sources.decode(request(source), ('{"total":1, "data":[' + text + ']}').encode())
    assert records[0].text == text
    assert records[0].position == 0


@pytest.mark.parametrize("source, field, valid", [
    (NSE, "an_dt", "10-Sep-2026 12:30:00"),
    (BSE, "exdate", "10 Sep 2026"),
    (BSE, "Ex_date", "2026-09-10T00:00:00"),
])
def test_native_date_bounds_and_missing_or_unparseable_dates(source, field, valid):
    row: dict[str, Any] = {"sm_isin": BOND} if source == NSE else {"scrip_code": 977935}
    row[field] = valid
    assert len(sources.decode(request(source), encoded([row]))) == 1
    with pytest.raises(sources.InvalidResponse, match="date_out_of_range"):
        sources.decode(request(source, start=date(2026, 9, 11), end=date(2026, 9, 12)), encoded([row]))
    with pytest.raises(sources.InvalidResponse, match="date_out_of_range"):
        sources.decode(request(source, start=date(2026, 9, 8), end=date(2026, 9, 9)), encoded([row]))
    for invalid in (None, "", "not a date", "31-Feb-2026", 12345678):
        row[field] = invalid
        with pytest.raises(sources.InvalidResponse):
            sources.decode(request(source), encoded([row]))
    row.pop(field)
    with pytest.raises(sources.InvalidResponse, match="missing_date"):
        sources.decode(request(source), encoded([row]))


def test_bse_conflicting_date_aliases_are_not_silently_ignored():
    with pytest.raises(sources.InvalidResponse, match="conflicting_dates"):
        sources.decode(request(BSE), encoded([bse_row(Ex_date="11 Sep 2026")]))


@pytest.mark.parametrize("metadata", [
    {"total": 2}, {"totalCount": 2}, {"totalRecords": "2"}, {"recordsTotal": 2},
    {"hasMore": True}, {"has_more": True}, {"truncated": True},
    {"isTruncated": True}, {"capped": True}, {"limit": 1},
])
def test_advertised_incompleteness_is_not_success(metadata):
    with pytest.raises(sources.IncompleteResponse):
        sources.decode(request(), encoded({"data": [nse_row()], **metadata}))


@pytest.mark.parametrize("metadata", [
    {"total": 0}, {"total": "unknown"}, {"total": -1}, {"total": True},
    {"hasMore": "unclear"}, {"limit": 0},
])
def test_invalid_completeness_metadata_is_not_success(metadata):
    with pytest.raises(sources.InvalidResponse):
        sources.decode(request(), encoded({"data": [nse_row()], **metadata}))


def test_no_invented_array_size_threshold_and_row_totals_are_honored():
    assert len(sources.decode(request(), encoded([nse_row()] * 1000))) == 1000
    with pytest.raises(sources.IncompleteResponse):
        sources.decode(request(), encoded([nse_row(totalRecords=2)]))


def test_decoder_size_guard(monkeypatch):
    monkeypatch.setattr(sources, "MAX_RESPONSE_BYTES", 3)
    with pytest.raises(sources.IncompleteResponse, match="response_too_large"):
        sources.decode(request(), b"[  ]")


def native(row):
    return sources.NativeRecord(0, json.dumps(row), row)


def test_nse_admits_unknown_categories_and_retains_only_verified_associations():
    row = nse_row(
        desc="An unrecognized disclosure category", unknown={"untouched": [1, None]},
        attchmntText=f"Other bonds: {OTHER_BOND}; {EQUITY}; {CP}; INE003A09010",
    )
    record = native(row)
    associations, reason = sources.admit(NSE, record, {BOND, OTHER_BOND, EQUITY, CP}, {})
    assert reason is None
    assert associations == [
        {"isin": BOND, "method": "source_isin", "evidence": {"field": "sm_isin", "value": BOND}},
        {"isin": OTHER_BOND, "method": "text_isin", "evidence": {
            "field": "attchmntText", "value": OTHER_BOND, "start": 13, "end": 25,
        }},
    ]
    assert record.data == row
    assert json.loads(record.text)["unknown"] == {"untouched": [1, None]}


@pytest.mark.parametrize("fields", [
    {"sm_isin": None, "sm_name": "Known issuer", "desc": "Interest payment"},
    {"sm_isin": EQUITY}, {"sm_isin": CP}, {"sm_isin": OTHER_BOND},
    {"sm_isin": f"prefix{BOND}"}, {"sm_isin": [BOND]},
    {"sm_isin": None, "attchmntText": f"X{BOND} {BOND}X _{BOND} {BOND}_"},
    {"sm_isin": None, "desc": BOND},
])
def test_nse_excludes_unidentified_equity_cp_off_universe_and_substring_guesses(fields):
    assert sources.admit(NSE, native(nse_row(**fields)), {BOND, EQUITY, CP}, {}) == ([], "unmatched_scope")


def test_nse_text_token_matches_are_deduplicated_and_explicit_evidence_wins():
    record = native(nse_row(attchmntText=f"({BOND}),{OTHER_BOND};{OTHER_BOND}"))
    associations, _ = sources.admit(NSE, record, {BOND, OTHER_BOND}, {})
    assert [(a["isin"], a["method"]) for a in associations] == [(BOND, "source_isin"), (OTHER_BOND, "text_isin")]
    assert sources.admit(NSE, native(nse_row(sm_isin=" " + BOND + " ")), {BOND}, {})[0][0]["isin"] == BOND


@pytest.mark.parametrize("code", [977935, "977935", " 977935 "])
def test_bse_requires_unambiguous_master_evidence_not_purpose(code):
    record = native(bse_row(scrip_code=code, purpose="Unrecognized source category"))
    associations, reason = sources.admit(BSE, record, {BOND}, {"977935": {BOND}})
    assert reason is None
    assert associations == [{"isin": BOND, "method": "bse_master", "evidence": {
        "field": "scrip_code", "value": "977935", "mapped_isin": BOND,
    }}]


@pytest.mark.parametrize("mapping", [{BOND, OTHER_BOND}, {BOND, EQUITY}, {BOND, "retired_identifier"}])
def test_bse_mapping_conflicts_include_noncurrent_instruments(mapping):
    assert sources.admit(BSE, native(bse_row()), {BOND}, {"977935": mapping}) == ([], "ambiguous_mapping")


@pytest.mark.parametrize("code", [None, "", "977935X", 977935.0, True, "9.77935e5"])
def test_bse_rejects_non_integer_codes(code):
    assert sources.admit(BSE, native(bse_row(scrip_code=code)), {BOND}, {"977935": {BOND}}) == ([], "unmatched_scope")


@pytest.mark.parametrize("mapping", [{}, {"977935": {EQUITY}}, {"977935": {OTHER_BOND}}])
def test_bse_does_not_infer_from_purpose_name_isin_or_code_ranges(mapping):
    record = native(bse_row(purpose="Interest / Redemption", long_name="Known issuer", sm_isin=BOND))
    assert sources.admit(BSE, record, {BOND}, mapping) == ([], "unmatched_scope")


@pytest.mark.parametrize("source, row, mappings", [
    (NSE, nse_row(), {}), (BSE, bse_row(), {"977935": {BOND}}),
])
def test_issue_date_guard_is_optional_and_conservative(source, row, mappings):
    record = native(row)
    assert sources.admit(source, record, {BOND}, mappings)[1] is None
    assert sources.admit(source, record, {BOND}, mappings, {BOND: DAY})[1] is None
    assert sources.admit(source, record, {BOND}, mappings, {BOND: date(2026, 9, 11)}) == ([], "predates_issue")


def test_multi_isin_issue_guard_drops_only_suspicious_association():
    record = native(nse_row(attchmntText=OTHER_BOND))
    associations, reason = sources.admit(NSE, record, {BOND, OTHER_BOND}, {}, {BOND: date(2026, 9, 11)})
    assert reason is None
    assert [a["isin"] for a in associations] == [OTHER_BOND]


def test_nse_metadata_indexes_native_fields_and_ist_without_interpretation():
    row = nse_row(
        seq_id=12345678901234567890, orgid="issuer-1", sm_name="Issuer",
        desc="Unrecognized source category", attchmntText="Unclassified source headline",
        attchmntFile="https://nsearchives.nseindia.com/announcements/file.pdf",
        unknown={"retained": True}, payment_date="not interpreted",
    )
    assert sources.metadata(NSE, row) == {
        "source_record_id": "12345678901234567890", "source_scrip_code": None,
        "source_issuer_id": "issuer-1", "issuer_name": "Issuer",
        "category_raw": "Unrecognized source category", "headline": "Unclassified source headline",
        "announced_at": datetime(2026, 9, 10, 7, 0, tzinfo=UTC),
        "source_links": ["https://nsearchives.nseindia.com/announcements/file.pdf"],
    }
    assert row["unknown"] == {"retained": True}


def test_bse_metadata_does_not_treat_exdate_or_payment_as_announcement():
    values = sources.metadata(BSE, bse_row(
        long_name="Issuer", purpose="Unknown category", PAYMENT_DATE="2026-09-12",
    ))
    assert values == {
        "source_record_id": None, "source_scrip_code": "977935", "source_issuer_id": None,
        "issuer_name": "Issuer", "category_raw": "Unknown category", "headline": "Unknown category",
        "announced_at": None, "source_links": [],
    }


@pytest.mark.parametrize("value", [None, "", "bad date", "10-Sep-2026", "2026-09-10"])
def test_metadata_does_not_invent_an_announcement_time(value):
    assert sources.metadata(NSE, nse_row(an_dt=value))["announced_at"] is None


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:text/html,unsafe", "//host/file", "/file.pdf",
    "https://user:secret@host/file", "https:///file", "https://host/\r\nunsafe",
    "https://host:bad/file", "https://host/with space", "https://host\\evil/file",
])
def test_metadata_links_must_be_safe_absolute_http_urls(url):
    assert sources.metadata(NSE, nse_row(attchmntFile=url))["source_links"] == []


def test_metadata_handles_malformed_scalars_without_stringifying_unknown_objects():
    values = sources.metadata(NSE, nse_row(seq_id={"private": "value"}, orgid=False, sm_name=["Issuer"], desc={}))
    assert values["source_record_id"] is None
    assert values["source_issuer_id"] is None
    assert values["issuer_name"] is None
    assert values["category_raw"] is None


def test_pathological_json_numbers_are_rejected_with_safe_codes():
    body = b'[{"an_dt":"10-Sep-2026 12:30:00","sm_isin":null,"unknown":' + b"9" * 5000 + b"}]"
    with pytest.raises(sources.InvalidResponse, match="invalid_json"):
        sources.decode(request(), body)


def test_metadata_datetime_overflow_does_not_break_indexing():
    assert sources.metadata(NSE, nse_row(an_dt="0001-01-01T00:00:00"))["announced_at"] is None


class FakeHTTPResponse:
    def __init__(self, status=200, chunks=(b"[]",), headers=None, url=None):
        self.status_code = status
        self.chunks = chunks
        self.headers = CaseInsensitiveDict(headers or {"Content-Type": "application/json"})
        self.url = url if url is not None else request().url
        self.closed = False
        self.iterated = 0
        self.chunk_sizes = []

    def iter_content(self, chunk_size):
        self.chunk_sizes.append(chunk_size)
        for chunk in self.chunks:
            self.iterated += 1
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, result):
        self.result = result
        self.headers = {}
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self):
        self.closed = True


def test_response_defaults():
    assert sources.Response(None) == sources.Response(None, b"", "", "", None, None)


def test_transport_reuses_one_session_per_source_and_makes_one_attempt_per_get():
    sessions = []

    def factory():
        session = FakeSession(FakeHTTPResponse())
        sessions.append(session)
        return session

    client = sources.ExchangeClient(session_factory=factory)
    first = client.get(request())
    assert first.status_code == 200
    assert first.body == b"[]"
    assert first.error is None
    assert first.content_type == "application/json"
    assert first.final_url == request().url
    client.get(request())
    client.get(request(BSE))
    assert len(sessions) == 2
    assert [len(session.calls) for session in sessions] == [2, 1]
    for source, session in zip(sources.SOURCES, sessions, strict=True):
        url, kwargs = session.calls[0]
        assert url == request(source).url
        assert kwargs["params"] == request(source).params
        assert kwargs["stream"] is True
        assert kwargs["allow_redirects"] is False
        assert kwargs["timeout"] == (10.0, 30.0)
        assert "Mozilla/" in session.headers["User-Agent"]
        assert session.headers["Referer"].startswith("https://www.")
        assert session.result.closed
    client.close()
    client.close()
    assert all(session.closed for session in sessions)


@pytest.mark.parametrize("status", [301, 403, 429, 500, 503])
def test_transport_reports_http_failures_without_retry_or_access_bypass(status, caplog):
    response = FakeHTTPResponse(status, chunks=(b"private challenge",), headers={
        "Content-Type": "text/html; charset=utf-8", "Retry-After": "12",
        "Set-Cookie": "private session", "Location": "https://other.invalid/?token=secret",
    })
    session = FakeSession(response)
    client = sources.ExchangeClient(session_factory=lambda: session)
    result = client.get(request())
    assert len(session.calls) == 1
    assert result.status_code == status
    assert result.error is None
    assert result.retry_after == 12.0
    assert result.content_type == "text/html"
    assert response.closed
    assert "private" not in caplog.text
    assert "secret" not in caplog.text
    client.close()


@pytest.mark.parametrize("error, code", [
    (requests.ConnectTimeout("private connect"), "timeout"),
    (requests.ReadTimeout("private read"), "timeout"),
    (requests.ConnectionError("private connection"), "connection_error"),
    (requests.RequestException("private transport"), "transport_error"),
])
def test_transport_errors_are_safe_and_never_retried(error, code, caplog):
    session = FakeSession(error)
    client = sources.ExchangeClient(session_factory=lambda: session)
    result = client.get(request())
    assert len(session.calls) == 1
    assert result == sources.Response(None, error=code)
    assert "private" not in repr(result)
    assert "private" not in caplog.text
    client.close()


def test_streaming_error_preserves_partial_body_and_http_metadata_but_signals_failure():
    response = FakeHTTPResponse(chunks=(b"[", requests.ReadTimeout("private content")))
    client = sources.ExchangeClient(session_factory=lambda: FakeSession(response))
    result = client.get(request())
    assert result.status_code == 200
    assert result.body == b"["
    assert result.error == "timeout"
    assert result.content_type == "application/json"
    assert response.closed
    client.close()


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "1"}, {"Content-Encoding": "gzip"}])
def test_streamed_response_size_guard_handles_absent_false_and_compressed_lengths(headers):
    response = FakeHTTPResponse(chunks=(b"123", b"456", b"must not read"), headers=headers)
    client = sources.ExchangeClient(session_factory=lambda: FakeSession(response), max_response_bytes=5)
    result = client.get(request())
    assert result.error == "response_too_large"
    assert result.status_code == 200
    assert len(result.body) <= 5
    assert response.iterated == 2
    assert response.closed
    client.close()


def test_advertised_oversize_is_rejected_without_buffering_or_consuming_body():
    response = FakeHTTPResponse(headers={"Content-Length": "6"})
    client = sources.ExchangeClient(session_factory=lambda: FakeSession(response), max_response_bytes=5)
    result = client.get(request())
    assert result.error == "response_too_large"
    assert result.body == b""
    assert response.iterated == 0
    assert response.closed
    client.close()


def test_exact_byte_bound_is_complete_and_empty_chunks_are_ignored():
    response = FakeHTTPResponse(chunks=(b"", b"[", b"]"), headers={"Content-Length": "2"})
    client = sources.ExchangeClient(session_factory=lambda: FakeSession(response), max_response_bytes=2)
    result = client.get(request())
    assert result.body == b"[]"
    assert result.error is None
    client.close()


@pytest.mark.parametrize("header", ["-1", "NaN", "inf", "private header", ""])
def test_invalid_retry_after_does_not_escape_into_diagnostics(header):
    response = FakeHTTPResponse(headers={"Retry-After": header})
    client = sources.ExchangeClient(session_factory=lambda: FakeSession(response))
    assert client.get(request()).retry_after is None
    client.close()


def test_retry_after_supports_http_date_without_sleeping():
    response = FakeHTTPResponse(headers={"Retry-After": "Thu, 01 Jan 2099 00:00:00 GMT"})
    client = sources.ExchangeClient(session_factory=lambda: FakeSession(response))
    result = client.get(request())
    assert result.retry_after is not None and result.retry_after > 0
    client.close()


def test_unsafe_response_metadata_is_redacted():
    response = FakeHTTPResponse(url=request().url + "?token=private&index=debt#private", headers={
        "Content-Type": "text/html\r\nprivate-header", "Set-Cookie": "private",
    })
    client = sources.ExchangeClient(session_factory=lambda: FakeSession(response))
    result = client.get(request())
    assert "private" not in repr(result)
    assert result.final_url == request().url + "?index=debt"
    assert result.content_type == ""
    client.close()


@pytest.mark.parametrize("url", ["https://private.invalid/challenge", "javascript:private", "https://user:private@www.nseindia.com/"])
def test_unexpected_final_urls_are_not_retained(url):
    response = FakeHTTPResponse(url=url)
    client = sources.ExchangeClient(session_factory=lambda: FakeSession(response))
    assert client.get(request()).final_url == ""
    client.close()


@pytest.mark.parametrize("bound", [0, -1, True])
def test_response_byte_bound_must_be_positive_integer(bound):
    with pytest.raises(ValueError):
        sources.ExchangeClient(max_response_bytes=bound)


def test_huge_content_length_is_safe_and_never_converted_to_an_unbounded_integer():
    response = FakeHTTPResponse(headers={"Content-Length": "9" * 5000})
    client = sources.ExchangeClient(session_factory=lambda: FakeSession(response))
    assert client.get(request()).error == "response_too_large"
    assert response.closed
    client.close()


def test_short_identity_body_is_not_complete_even_if_stream_ends_cleanly():
    response = FakeHTTPResponse(headers={"Content-Length": "10"})
    client = sources.ExchangeClient(session_factory=lambda: FakeSession(response))
    result = client.get(request())
    assert result.error == "incomplete_body"
    assert result.body == b"[]"
    client.close()


def test_response_repr_cannot_accidentally_log_response_body():
    assert "private" not in repr(sources.Response(403, b"private challenge"))


def test_native_record_repr_cannot_accidentally_log_excluded_content():
    assert "private" not in repr(native({"private": "unmatched row"}))
