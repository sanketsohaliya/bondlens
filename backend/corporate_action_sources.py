import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import requests

from . import bse, nse

SOURCES = ("nse_debt_announcements", "bse_corporate_actions")
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_IST = timezone(timedelta(hours=5, minutes=30))
_WHITESPACE = re.compile(r"[ \t\r\n]*")
_TOTAL_KEYS = {"total", "totalcount", "totalrecords", "recordstotal"}
_FLAG_KEYS = {"hasmore", "truncated", "istruncated", "capped", "iscapped"}
_LIMIT_KEYS = {"limit", "pagesize"}


@dataclass(frozen=True)
class Request:
    source: str
    date_from: date
    date_to: date

    def __post_init__(self):
        if self.source not in SOURCES:
            raise ValueError("unknown_source")
        if (
            type(self.date_from) is not date
            or type(self.date_to) is not date
            or self.date_from > self.date_to
        ):
            raise ValueError("invalid_date_range")

    @property
    def url(self) -> str:
        if self.source == SOURCES[0]:
            return "https://www.nseindia.com/api/corporate-announcements"
        return "https://api.bseindia.com/BseIndiaAPI/api/DefaultData/w"

    @property
    def params(self) -> dict[str, str]:
        if self.source == SOURCES[0]:
            return {
                "index": "debt",
                "from_date": self.date_from.strftime("%d-%m-%Y"),
                "to_date": self.date_to.strftime("%d-%m-%Y"),
            }
        return {
            "Fdate": self.date_from.strftime("%Y%m%d"),
            "TDate": self.date_to.strftime("%Y%m%d"),
            "Purposecode": "",
            "ddlcategorys": "E",
            "ddlindustrys": "",
            "scripcode": "",
            "strSearch": "S",
            "segment": "Debt",
        }

    @property
    def axis(self) -> str:
        return "an_dt" if self.source == SOURCES[0] else "exdate"


@dataclass
class NativeRecord:
    position: int
    text: str = dataclass_field(repr=False)
    data: dict[str, Any] = dataclass_field(repr=False)


class InvalidResponse(ValueError):
    def __init__(self, code: str = "invalid_response"):
        self.code = code
        super().__init__(code)


class IncompleteResponse(InvalidResponse):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, value in pairs:
        if key in data:
            raise InvalidResponse("invalid_json")
        data[key] = value
    return data


def _invalid_constant(value: str) -> Any:
    raise InvalidResponse("invalid_json")


def _skip_space(text: str, position: int) -> int:
    match = _WHITESPACE.match(text, position)
    return match.end() if match else position


def _normalized_key(key: str) -> str:
    return key.replace("_", "").lower()


def _nonnegative_integer(value: Any) -> int:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    raise InvalidResponse("invalid_schema")


def _completeness(data: dict[str, Any], delivered: int) -> None:
    for field, value in data.items():
        key = _normalized_key(field)
        if key in _TOTAL_KEYS:
            total = _nonnegative_integer(value)
            if total > delivered:
                raise IncompleteResponse("advertised_total_exceeds_rows")
            if total < delivered:
                raise InvalidResponse("total_mismatch")
        elif key in _FLAG_KEYS:
            if value in (True, 1, "true", "True", "1"):
                raise IncompleteResponse("source_reports_incomplete")
            if value not in (False, 0, "false", "False", "0"):
                raise InvalidResponse("invalid_schema")
        elif key in _LIMIT_KEYS:
            limit = _nonnegative_integer(value)
            if not limit:
                raise InvalidResponse("invalid_schema")
            if delivered >= limit:
                raise IncompleteResponse("advertised_limit_reached")


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(" IST"):
        text = text[:-4]
    parsed = None
    if re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T ]|$)", text):
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            pass
    if parsed is None:
        for fmt in (
            "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M:%S.%f",
            "%d %b %Y %H:%M:%S", "%d-%m-%Y %H:%M:%S",
            "%d/%m/%Y %H:%M:%S", "%d-%b-%Y", "%d %b %Y",
            "%d-%m-%Y", "%d/%m/%Y", "%Y%m%d",
        ):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    return parsed.replace(tzinfo=_IST) if parsed.tzinfo is None else parsed


def _native_date(source: str, data: dict[str, Any]) -> date:
    fields = ("an_dt",) if source == SOURCES[0] else ("exdate", "Ex_date")
    dates = []
    for field in fields:
        if field not in data or data[field] is None or data[field] == "":
            continue
        parsed = _parse_datetime(data[field])
        if parsed is None:
            raise InvalidResponse("invalid_date")
        dates.append(parsed.astimezone(_IST).date())
    if not dates:
        raise InvalidResponse("missing_date")
    if len(set(dates)) != 1:
        raise InvalidResponse("conflicting_dates")
    return dates[0]


def decode(request: Request, body: bytes) -> list[NativeRecord]:
    if len(body) > MAX_RESPONSE_BYTES:
        raise IncompleteResponse("response_too_large")
    decoder = json.JSONDecoder(
        object_pairs_hook=_strict_object, parse_constant=_invalid_constant
    )
    try:
        text = body.decode("utf-8")
        start = _skip_space(text, 0)
        root, end = decoder.raw_decode(text, start)
        if _skip_space(text, end) != len(text):
            raise InvalidResponse("invalid_json")
        array_start = start
        if isinstance(root, dict):
            if "data" not in root or any(
                key != "data"
                and _normalized_key(key) not in _TOTAL_KEYS | _FLAG_KEYS | _LIMIT_KEYS
                for key in root
            ):
                raise InvalidResponse("invalid_schema")
            array_start = _skip_space(text, start + 1)
            while True:
                key, key_end = decoder.raw_decode(text, array_start)
                value_start = _skip_space(text, _skip_space(text, key_end) + 1)
                if key == "data":
                    array_start = value_start
                    break
                _, value_end = decoder.raw_decode(text, value_start)
                array_start = _skip_space(text, _skip_space(text, value_end) + 1)
            rows = root["data"]
        else:
            rows = root
        if not isinstance(rows, list):
            raise InvalidResponse("invalid_schema")
        if isinstance(root, dict):
            _completeness(root, len(rows))
        records = []
        position = _skip_space(text, array_start + 1)
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise InvalidResponse("invalid_schema")
            native_date = _native_date(request.source, row)
            required = (
                {"seq_id", "sm_isin", "desc", "attchmntText", "sm_name", "orgid"}
                if request.source == SOURCES[0] else {"scrip_code"}
            )
            if not required.intersection(row):
                raise InvalidResponse("invalid_schema")
            if not request.date_from <= native_date <= request.date_to:
                raise InvalidResponse("date_out_of_range")
            _completeness(row, len(rows))
            _, row_end = decoder.raw_decode(text, position)
            records.append(NativeRecord(index, text[position:row_end], row))
            position = _skip_space(text, _skip_space(text, row_end) + 1)
        return records
    except InvalidResponse:
        raise
    except (ValueError, RecursionError, OverflowError):
        raise InvalidResponse("invalid_json") from None


def _current_bond(isin: str, current_isins: set[str]) -> bool:
    return (
        isin in current_isins
        and re.fullmatch(r"INE[A-Z0-9]{9}", isin) is not None
        and isin[7:9] in {"07", "08", "09"}
    )


def _scrip_code(value: Any) -> str | None:
    if type(value) is int and value >= 0:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        return value.strip().lstrip("0") or "0"
    return None


def admit(
    source: str,
    record: NativeRecord,
    current_isins: set[str],
    bse_mappings: dict[str, set[str]],
    issue_dates: dict[str, date] | None = None,
) -> tuple[list[dict], str | None]:
    if source not in SOURCES:
        raise ValueError("unknown_source")
    data = record.data
    associations: dict[str, dict] = {}
    if source == SOURCES[0]:
        explicit = data.get("sm_isin")
        if isinstance(explicit, str) and _current_bond(explicit.strip(), current_isins):
            isin = explicit.strip()
            associations[isin] = {
                "isin": isin,
                "method": "source_isin",
                "evidence": {"field": "sm_isin", "value": explicit},
            }
        text = data.get("attchmntText")
        if isinstance(text, str):
            for match in re.finditer(r"(?<!\w)INE[A-Z0-9]{9}(?!\w)", text):
                isin = match.group()
                if _current_bond(isin, current_isins) and isin not in associations:
                    associations[isin] = {
                        "isin": isin,
                        "method": "text_isin",
                        "evidence": {
                            "field": "attchmntText", "value": isin,
                            "start": match.start(), "end": match.end(),
                        },
                    }
    else:
        code = _scrip_code(data.get("scrip_code"))
        mapped = bse_mappings.get(code, set()) if code is not None else set()
        if len(mapped) > 1:
            return [], "ambiguous_mapping"
        if len(mapped) == 1:
            isin = next(iter(mapped))
            if _current_bond(isin, current_isins):
                associations[isin] = {
                    "isin": isin,
                    "method": "bse_master",
                    "evidence": {
                        "field": "scrip_code", "value": code, "mapped_isin": isin,
                    },
                }
    if not associations:
        return [], "unmatched_scope"
    if issue_dates:
        try:
            native_date = _native_date(source, data)
        except InvalidResponse as exc:
            return [], exc.code
        associations = {
            isin: association for isin, association in associations.items()
            if isin not in issue_dates or native_date >= issue_dates[isin]
        }
        if not associations:
            return [], "predates_issue"
    return list(associations.values()), None


def _scalar(data: dict[str, Any], *fields: str) -> str | None:
    for field in fields:
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            return value
        if type(value) is int:
            return str(value)
    return None


def _safe_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value or re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        _ = parsed.port
    except ValueError:
        return None
    return value


def metadata(source: str, data: dict) -> dict:
    if source not in SOURCES:
        raise ValueError("unknown_source")
    announced_at = None
    if source == SOURCES[0]:
        value = data.get("an_dt")
        if isinstance(value, str) and re.search(r"\d{2}:\d{2}:\d{2}", value):
            parsed = _parse_datetime(value)
            if parsed is not None:
                try:
                    announced_at = parsed.astimezone(UTC)
                except OverflowError:
                    pass
        source_record_id = _scalar(data, "seq_id")
        source_issuer_id = _scalar(data, "orgid")
        source_scrip_code = None
        issuer_name = _scalar(data, "sm_name")
        category = _scalar(data, "desc")
        headline = _scalar(data, "attchmntText", "desc")
        link_fields = ("attchmntFile",)
    else:
        source_record_id = None
        source_issuer_id = None
        source_scrip_code = _scrip_code(data.get("scrip_code"))
        issuer_name = _scalar(data, "long_name", "Long_Name", "short_name")
        category = _scalar(data, "purpose", "Purpose")
        headline = category
        link_fields = ("attachment_url", "attachment", "url")
    links = []
    for field in link_fields:
        link = _safe_url(data.get(field))
        if link is not None and link not in links:
            links.append(link)
    return {
        "source_record_id": source_record_id,
        "source_scrip_code": source_scrip_code,
        "source_issuer_id": source_issuer_id,
        "issuer_name": issuer_name,
        "category_raw": category,
        "headline": headline,
        "announced_at": announced_at,
        "source_links": links,
    }


@dataclass
class Response:
    status_code: int | None
    body: bytes = dataclass_field(default=b"", repr=False)
    final_url: str = ""
    content_type: str = ""
    retry_after: float | None = None
    error: str | None = None


def _safe_final_url(value: Any, request: Request) -> str:
    safe = _safe_url(value)
    if safe is None:
        return ""
    actual = urlsplit(safe)
    expected = urlsplit(request.url)
    if (actual.scheme, actual.netloc, actual.path) != (
        expected.scheme, expected.netloc, expected.path
    ):
        return ""
    params = request.params
    query = urlencode([
        (key, value) for key, value in parse_qsl(actual.query, keep_blank_values=True)
        if key in params and value == params[key]
    ])
    return request.url + ("?" + query if query else "")


def _content_type(value: str) -> str:
    if re.search(r"[\x00-\x1f\x7f]", value):
        return ""
    media_type = value.partition(";")[0].strip().lower()
    if re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", media_type):
        return media_type
    return ""


def _retry_after(value: str) -> float | None:
    value = value.strip()
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        seconds = float(value)
        return seconds if math.isfinite(seconds) else None
    try:
        when = parsedate_to_datetime(value)
        if when.tzinfo is not None:
            return max(0.0, (when - datetime.now(UTC)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        pass
    return None


class ExchangeClient:
    def __init__(
        self,
        session_factory: Callable[[], Any] = requests.Session,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ):
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("invalid_response_byte_bound")
        self._session_factory = session_factory
        self._max_response_bytes = max_response_bytes
        self._sessions: dict[str, Any] = {}

    def get(self, request: Request) -> Response:
        if request.source not in self._sessions:
            session = self._session_factory()
            session.headers.update(nse.HEADERS if request.source == SOURCES[0] else bse.HEADERS)
            session.headers.update({"Accept": "application/json, text/plain, */*"})
            self._sessions[request.source] = session
        session = self._sessions[request.source]
        result = Response(None)
        raw = None
        body = bytearray()
        try:
            raw = session.get(
                request.url,
                params=request.params,
                timeout=(10.0, 30.0),
                stream=True,
                allow_redirects=False,
            )
            result.status_code = raw.status_code
            result.final_url = _safe_final_url(raw.url, request)
            result.content_type = _content_type(raw.headers.get("Content-Type", ""))
            result.retry_after = _retry_after(raw.headers.get("Retry-After", ""))
            length_header = raw.headers.get("Content-Length", "").strip()
            length = None
            if re.fullmatch(r"[0-9]+", length_header):
                digits = length_header.lstrip("0") or "0"
                length = (
                    self._max_response_bytes + 1
                    if len(digits) > len(str(self._max_response_bytes))
                    else int(digits)
                )
            if length is not None and length > self._max_response_bytes:
                result.error = "response_too_large"
            else:
                for chunk in raw.iter_content(chunk_size=min(65536, self._max_response_bytes + 1)):
                    if not chunk:
                        continue
                    remaining = self._max_response_bytes - len(body)
                    if len(chunk) > remaining:
                        body.extend(chunk[:remaining])
                        result.error = "response_too_large"
                        break
                    body.extend(chunk)
                if (
                    result.error is None
                    and length is not None
                    and len(body) < length
                    and raw.headers.get("Content-Encoding", "").lower() in {"", "identity"}
                ):
                    result.error = "incomplete_body"
        except requests.Timeout:
            result.error = "timeout"
        except requests.ConnectionError:
            result.error = "connection_error"
        except requests.RequestException:
            result.error = "transport_error"
        finally:
            if raw is not None:
                raw.close()
        result.body = bytes(body)
        return result

    def close(self) -> None:
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()
