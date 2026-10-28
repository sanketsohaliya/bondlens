import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from .analytics.schedule import Flow

VERSION = "2"
SOURCES = {"nse_debt_announcements": "NSE", "bse_corporate_actions": "BSE"}
NATURES = {
    "interest payment": (["interest"], None),
    "redemption": (["redemption"], None),
    "part principal payment": (["redemption"], "partial"),
    "interest payment and redemption": (["interest", "redemption"], None),
    "interest and part principal payment": (["interest", "redemption"], "partial"),
    "interest payment and part/partial principal amount repayment": (["interest", "redemption"], "partial"),
}
VOLATILE = {"difference", "exchdisstime", "dt", "sort_date", "fileSize", "attFileSize", "attchmntFile"}


def normalized(value: Any) -> str:
    return " ".join(str(value or "").split())


def safe_link(value: Any) -> str | None:
    if not isinstance(value, str) or re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        _ = parsed.port
    except ValueError:
        return None
    return value


def calendar_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value if value.year < 2500 else None
    if not isinstance(value, str):
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T.+", value.strip()):
        try:
            parsed = datetime.fromisoformat(value.strip()).date()
            return parsed if parsed.year < 2500 else None
        except ValueError:
            return None
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            parsed = datetime.strptime(value.strip().rstrip("."), fmt).date()
            return parsed if parsed.year < 2500 else None
        except ValueError:
            pass
    return None


def timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str):
        try:
            return timestamp(datetime.fromisoformat(value))
        except ValueError:
            pass
    return None


def source_availability(rows: list[dict], now: datetime) -> list[dict]:
    result = []
    for row in rows:
        target = row.get("target")
        last = timestamp(row.get("last_success_at"))
        reasons = []
        complete = bool(target and target.get("history_from") and not target.get("pending"))
        if target is None:
            status = "not_captured"
            reasons.append("no_capture_target")
        elif target.get("blocked_reason") == "no_verified_scrip":
            status = "mapping_blocked"
            reasons.append("no_verified_scrip")
        elif not target.get("active", True):
            status = "unavailable"
            reasons.append("capture_target_inactive")
        elif row.get("latest_ok") is False or (last and now - last > timedelta(hours=18)):
            status = "stale"
            reasons.append("latest_capture_failed" if row.get("latest_ok") is False else "capture_stale")
        elif last is None or not complete or target.get("recent_pending"):
            status = "incomplete"
            if last is None:
                reasons.append("no_successful_recent_capture")
            if not complete:
                reasons.append("history_incomplete")
            if target.get("recent_pending"):
                reasons.append("recent_capture_incomplete")
        else:
            status = "fresh"
        result.append({"source": SOURCES[row["source"]], "status": status,
                       "last_success_at": last, "history_complete": complete, "reasons": reasons})
    return result


def flow_key(flow: Flow) -> str:
    return "|".join([flow.event, flow.due_date.isoformat(),
                     flow.record_date.isoformat() if flow.record_date else "",
                     flow.payment_date.isoformat() if flow.payment_date else ""])


def _labeled_dates(text: str, label: str, reasons: list[str]) -> date | None:
    matches = re.findall(r"\b" + label + r"\s*-\s*([^,\r\n]+)", text, re.I)
    values = {v.strip().rstrip(".") for v in matches if v.strip() not in {"", "-", "--"}}
    if not values:
        return None
    parsed = {calendar_date(v) for v in values}
    if len(parsed) != 1 or None in parsed:
        reasons.append("invalid_or_ambiguous_" + label.lower().replace(" ", "_"))
        return None
    return next(iter(parsed))


def _notice(isin: str, row: dict) -> dict | None:
    source = SOURCES.get(row.get("source") or "")
    if source is None:
        return None
    raw = row.get("raw") or {}
    headline = str(row.get("headline") or raw.get("attchmntText") or row.get("category_raw") or "")
    category = str(row.get("category_raw") or raw.get("desc") or raw.get("Purpose") or "")
    notice: dict[str, Any] = {
        "id": "", "source": source, "source_record_ids": [], "raw_ids": [row.get("raw_id", row["id"])],
        "representation_count": 1, "category": category, "headline": headline,
        "event_isin": None, "mentioned_isins": sorted(set(re.findall(r"\bIN[A-Z0-9]{10}\b", headline))),
        "events": [], "redemption_form": None, "nature": None, "due_date": None,
        "record_date": None, "ex_date": None, "reported_payment_date": None,
        "published_at": timestamp(row.get("announced_at")), "first_captured_at": timestamp(row.get("first_seen_at")),
        "last_captured_at": timestamp(row.get("last_seen_at") or row.get("first_seen_at")),
        "links": [], "status": "unmatched", "reasons": [], "flow_keys": [], "unmatched_events": [],
        "corroborated_by": None,
    }
    sid = row.get("source_record_id")
    if sid is not None:
        notice["source_record_ids"] = [str(sid)]
    for candidate in [*(row.get("attachment_urls") or []), row.get("source_url"), raw.get("attchmntFile")]:
        link = safe_link(candidate)
        if link and link not in notice["links"]:
            notice["links"].append(link)
    reasons = notice["reasons"]
    if source == "NSE":
        clauses = re.split(r"\bNature of Payment\s*-\s*", headline, flags=re.I)
        if len(clauses) != 2:
            reasons.append("unparsed_payment_clause")
        else:
            clause = clauses[1]
            notice["nature"] = clause.split(",", 1)[0].strip()
            nature = NATURES.get(normalized(notice["nature"]).lower())
            if nature:
                notice["events"], notice["redemption_form"] = nature
            else:
                reasons.append("unrecognized_nature")
            isin_values = {v.strip().rstrip(".").upper() for v in re.findall(r"\bISIN\s*-\s*([^,\r\n]+)", clause, re.I)}
            primary = {v for v in isin_values if re.fullmatch(r"IN[A-Z0-9]{10}", v)}
            if len(primary) == 1 and primary == isin_values:
                notice["event_isin"] = next(iter(primary))
                explicit = normalized(raw.get("sm_isin")).upper()
                if explicit and explicit != notice["event_isin"]:
                    reasons.append("identifier_conflict")
                if notice["event_isin"] != isin:
                    notice["status"] = "context_only"
                    reasons.append("event_applies_to_another_isin")
            else:
                reasons.append("ambiguous_event_isin")
            notice["due_date"] = _labeled_dates(clause, "Due date", reasons)
            notice["record_date"] = _labeled_dates(clause, "Record date", reasons)
            notice["reported_payment_date"] = _labeled_dates(clause, "Payment date", reasons)
            if notice["due_date"] is None:
                reasons.append("due_date_not_supplied")
        if not any(part in category.lower() for part in ("record date", "prior intimation", "intimation for payment")):
            reasons.append("not_an_upcoming_payment_notice")
    else:
        purposes = {
            "payment of interest": (["interest"], None),
            "payment of interest for bonds": (["interest"], None),
            "redemption of ncd": (["redemption"], None),
            "redemption of bonds": (["redemption"], None),
            "redemption(part) of ncd": (["redemption"], "partial"),
            "redemption (part) of bonds": (["redemption"], "partial"),
            "early redemption of ncd": (["redemption"], "early"),
        }
        purpose = purposes.get(normalized(category).lower())
        notice["nature"] = category
        notice["event_isin"] = isin if row.get("verified_bse") else None
        notice["mentioned_isins"] = [isin] if row.get("verified_bse") else []
        if not row.get("verified_bse"):
            reasons.append("unverified_bse_identity")
        if purpose:
            notice["events"], notice["redemption_form"] = purpose
            if notice["redemption_form"] == "early":
                reasons.append("early_redemption_review")
        else:
            reasons.append("purpose_requires_review")
        for field, names in (("record_date", ("RD_Date",)), ("ex_date", ("exdate", "Ex_date"))):
            values = [raw[name] for name in names if raw.get(name) not in (None, "", "-", "--")]
            parsed = {calendar_date(value) for value in values}
            if values and (None in parsed or len(parsed) != 1):
                reasons.append("invalid_or_ambiguous_" + field)
            elif parsed:
                notice[field] = next(iter(parsed))
        if notice["record_date"] is None:
            reasons.append("record_date_not_supplied")
        code = str(raw.get("scrip_code") or row.get("scrip_code") or "")
        if row.get("verified_bse") and re.fullmatch(r"[0-9]+", code):
            notice["links"].append(f"https://www.bseindia.com/stock-share-price/debt-other/scripcode/{code}/corp-actions")
    if reasons and notice["status"] != "context_only":
        notice["status"] = "review_only"
    semantic = {k: v for k, v in raw.items() if k not in VOLATILE and not (sid is not None and k == "an_dt")}
    digest = hashlib.sha256(json.dumps([source, sid, normalized(headline), normalized(category), semantic],
                                      sort_keys=True, default=str).encode()).hexdigest()
    notice["id"] = source.lower() + "-" + digest[:24]
    return notice


def _group_notices(notices: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for notice in notices:
        current = grouped.get(notice["id"])
        if current is None:
            grouped[notice["id"]] = notice
            continue
        current["raw_ids"] = sorted(set(current["raw_ids"] + notice["raw_ids"]))
        current["representation_count"] = len(current["raw_ids"])
        current["links"] = sorted(set(current["links"] + notice["links"]))
        for key, select in (("published_at", min), ("first_captured_at", min), ("last_captured_at", max)):
            dates = [value for value in (current[key], notice[key]) if value is not None]
            current[key] = select(dates) if dates else None
    identities: dict[tuple, list[dict]] = defaultdict(list)
    for notice in grouped.values():
        for source_id in notice["source_record_ids"]:
            identities[(notice["source"], source_id)].append(notice)
    for versions in identities.values():
        details_differ = len({(n["event_isin"], tuple(n["events"]), n["redemption_form"])
                              for n in versions if n["event_isin"] and n["events"]}) > 1
        dates_differ = any(len({n[field] for n in versions if n[field] is not None}) > 1
                           for field in ("due_date", "record_date", "reported_payment_date"))
        if details_differ or dates_differ:
            for notice in versions:
                if "competing_versions" not in notice["reasons"]:
                    notice["reasons"].append("competing_versions")
    return sorted(grouped.values(), key=lambda n: (n["source"], n["due_date"] or n["record_date"] or date.max, n["id"]))


def _field(field: str, nsdl: date | None, notices: list[dict]) -> dict:
    values = sorted({n[field] for n in notices if n.get(field) is not None})
    if len(values) > 1:
        state = "conflicting"
    elif not values or nsdl is None:
        state = "not_supplied"
    else:
        state = "agree" if values[0] == nsdl else "different"
    return {"field": field, "state": state, "nsdl_date": nsdl, "exchange_dates": values}


# A notice and an NSDL row are the same payment when their anchor dates are
# within this many days: exchanges and the depository publish record dates
# from different calendars (INE532F07EM7: BSE 09 Oct, NSDL 11 Oct). Exact
# agreement is preferred; within the window a unique same-event row is
# accepted and the difference surfaces as a conflict instead of leaving the
# notice unmatched. Never a nearest-date guess beyond the window.
MATCH_WINDOW_DAYS = 10


def _candidates(flows: list[Flow], notice: dict, event: str) -> list[Flow]:
    anchor = notice["due_date"] if notice["source"] == "NSE" else notice["record_date"]
    if anchor is None:
        return []
    def gap(flow: Flow) -> int | None:
        mine = flow.due_date if notice["source"] == "NSE" else flow.record_date
        return None if mine is None else abs((mine - anchor).days)
    same = [f for f in flows if f.event == event]
    exact = [f for f in same if gap(f) == 0]
    if exact:
        return exact
    return [f for f in same if (g := gap(f)) is not None and g <= MATCH_WINDOW_DAYS]


def record_date_overrides(isin: str, flows: list[Flow], records: list[dict]) -> dict[str, dict]:
    """flow_key -> {record_date, source, previous, notice_id} for every flow
    whose matched exchange notice states a different record date (BSE) or
    ex-date when that is all it carries. Only clean notices (no review
    reasons) are trusted; the most recently captured version wins. This is
    the eligibility overlay the schedule applies - the exchange's register
    date is the one the depository actually uses."""
    notices = _group_notices([n for row in records if (n := _notice(isin, row)) is not None])
    keys: dict[str, list[Flow]] = defaultdict(list)
    for flow in flows:
        keys[flow_key(flow)].append(flow)
    out: dict[str, dict] = {}
    for notice in sorted(notices, key=lambda n: n["last_captured_at"] or datetime.min.replace(tzinfo=UTC)):
        if notice["status"] != "unmatched" or notice["reasons"]:
            continue
        stated = notice["record_date"] or notice["ex_date"]
        if stated is None:
            continue
        for event in notice["events"]:
            candidates = _candidates(flows, notice, event)
            if len(candidates) != 1 or len(keys[flow_key(candidates[0])]) != 1:
                continue
            flow = candidates[0]
            if flow.record_date == stated:
                continue
            out[flow_key(flow)] = {"record_date": stated, "source": notice["source"],
                                   "previous": flow.record_date, "notice_id": notice["id"]}
    return out


def build_evidence(isin: str, flows: list[Flow], records: list[dict], *, as_of: date,
                   date_to: date | None = None, date_from: date | None = None,
                   availability: list[dict] | None = None, partial: bool = False) -> dict:
    start = date_from or as_of
    end = date_to or start + timedelta(days=90)
    if end < start:
        raise ValueError("Evidence window must not end before its start")
    notices = _group_notices([n for row in records if (n := _notice(isin, row)) is not None])
    blocked = {s["source"] for s in availability or [] if s["status"] == "mapping_blocked"}
    for notice in notices:
        reason = "partial_evidence_set" if partial else "source_mapping_blocked" if notice["source"] in blocked else None
        if reason:
            notice["reasons"].append(reason)
            if notice["status"] != "context_only":
                notice["status"] = "review_only"
    keys: dict[str, list[Flow]] = defaultdict(list)
    for flow in flows:
        keys[flow_key(flow)].append(flow)
    matches: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for notice in notices:
        if notice["status"] != "unmatched" or partial:
            continue
        for event in notice["events"]:
            candidates = _candidates(flows, notice, event)
            if len(candidates) == 1 and len(keys[flow_key(candidates[0])]) == 1:
                key = flow_key(candidates[0])
                notice["flow_keys"].append(key)
                matches[key][notice["source"]].append(notice)
            else:
                notice["unmatched_events"].append(event)
                if candidates:
                    notice["status"] = "ambiguous"
                    notice["reasons"].append("multiple_nsdl_candidates")
        if notice["flow_keys"] and notice["status"] == "unmatched":
            if notice["unmatched_events"]:
                notice["reasons"].append("partially_matched_notice")
            else:
                notice["status"] = "matched"
    # Second pass: a BSE notice carries only a record date, so when that date
    # disagrees with NSDL it has nothing exact to anchor on. If an NSE notice
    # already matched a row (on due date) and states the same record date the
    # BSE notice does, the two exchanges corroborate each other and the BSE
    # notice joins that row. No nearest-date guessing is involved.
    for notice in notices:
        if notice["source"] != "BSE" or notice["status"] != "unmatched" or partial:
            continue
        if notice["record_date"] is None or notice["flow_keys"]:
            continue
        for event in list(notice["unmatched_events"]):
            rows = [key for key, by_source in matches.items()
                    if by_source["NSE"] and keys[key][0].event == event
                    and any(n["record_date"] == notice["record_date"] for n in by_source["NSE"])]
            if len(rows) != 1:
                continue
            key = rows[0]
            notice["flow_keys"].append(key)
            notice["unmatched_events"].remove(event)
            notice["corroborated_by"] = "NSE"
            matches[key]["BSE"].append(notice)
        if notice["flow_keys"]:
            notice["reasons"] = [r for r in notice["reasons"] if r != "multiple_nsdl_candidates"]
            if notice["unmatched_events"]:
                notice["reasons"].append("partially_matched_notice")
            else:
                notice["status"] = "matched"
    visible = set()
    checked = {key for key, group in keys.items() if any(start <= f.effective_date <= end for f in group)}
    for notice in notices:
        dates = [notice[k] for k in ("due_date", "record_date", "ex_date") if notice[k] is not None]
        lower = date.fromordinal(max(1, start.toordinal() - 30)) if notice["source"] == "BSE" else start
        in_window = any(lower <= d <= end for d in dates)
        in_window = in_window or bool(checked.intersection(notice["flow_keys"]))
        if not dates:
            published = notice["published_at"]
            in_window = published is None or date.fromordinal(max(1, as_of.toordinal() - 30)) <= published.date() <= end
        if in_window:
            visible.add(notice["id"])
            checked.update(notice["flow_keys"])
    while True:
        linked = [n for n in notices if n["id"] not in visible and checked.intersection(n["flow_keys"])]
        if not linked:
            break
        for notice in linked:
            visible.add(notice["id"])
            checked.update(notice["flow_keys"])
    versions = {(n["source"], sid) for n in notices if n["id"] in visible
                and "competing_versions" in n["reasons"] for sid in n["source_record_ids"]}
    for notice in notices:
        if notice["id"] not in visible and any(
            (notice["source"], sid) in versions for sid in notice["source_record_ids"]
        ):
            visible.add(notice["id"])
            notice["reasons"].append("related_version_outside_window")
    out = []
    for key, group in keys.items():
        flow = group[0]
        row = {"key": key, "event": flow.event, "due_date": flow.due_date,
               "record_date": flow.record_date, "payment_date": flow.payment_date,
               "checked": key in checked, "ambiguous": len(group) > 1, "sources": []}
        for source, attached in matches[key].items():
            attached = [n for n in attached if n["id"] in visible]
            if not attached or key not in checked:
                continue
            fields = [_field("due_date", flow.due_date, attached), _field("record_date", flow.record_date, attached),
                      {"field": "payment_date", "state": "not_supplied", "nsdl_date": flow.payment_date, "exchange_dates": []},
                      {"field": "amount", "state": "not_supplied", "nsdl_date": None, "exchange_dates": []}]
            anchor = ("due_date" if source == "NSE"
                      else "nse_record_date" if all(n["corroborated_by"] == "NSE" for n in attached)
                      else "record_date")
            row["sources"].append({"source": source, "anchor": anchor, "notice_ids": sorted({n["id"] for n in attached}),
                                   "state": "conflicting" if any(f["state"] == "conflicting" for f in fields)
                                   or any("competing_versions" in n["reasons"] for n in attached) else "matched",
                                   "fields": fields})
        out.append(row)
    shown = [n for n in notices if n["id"] in visible]
    return {"isin": isin, "as_of": as_of, "date_from": start, "date_to": end, "version": VERSION,
            "partial": partial, "sources": availability or [], "flows": out, "notices": shown,
            "unmatched_notice_ids": [n["id"] for n in shown if n["status"] != "matched"],
            "summary": {"checked_flows": len(checked), "matched_flows": sum(bool(r["sources"]) for r in out),
                        "differences": sum(any(s["state"] == "conflicting" or any(f["state"] in {"different", "conflicting"}
                                               for f in s["fields"]) for s in r["sources"]) for r in out),
                        "unmatched_notices": sum(n["status"] != "matched" for n in shown)}}
