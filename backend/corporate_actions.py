import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from time import sleep as time_sleep
from typing import Any

from . import db
from .corporate_action_sources import (
    SOURCES,
    IncompleteResponse,
    InvalidResponse,
    Request,
    admit,
    decode,
    metadata,
)

EXTRACTOR_VERSION = "1"


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def merge_windows(windows: list[dict]) -> list[dict]:
    pairs = sorted((date.fromisoformat(w["from"]), date.fromisoformat(w["to"])) for w in windows)
    result: list[dict] = []
    for start, end in pairs:
        if start > end:
            continue
        if result and start <= date.fromisoformat(result[-1]["to"]) + timedelta(days=1):
            result[-1]["to"] = max(end, date.fromisoformat(result[-1]["to"])).isoformat()
        else:
            result.append({"from": start.isoformat(), "to": end.isoformat()})
    return result


def subtract_window(windows: list[dict], start: date, end: date) -> list[dict]:
    result = []
    for window in windows:
        left, right = date.fromisoformat(window["from"]), date.fromisoformat(window["to"])
        if right < start or left > end:
            result.append(window)
            continue
        if left < start:
            result.append({"from": left.isoformat(), "to": (start - timedelta(days=1)).isoformat()})
        if right > end:
            result.append({"from": (end + timedelta(days=1)).isoformat(), "to": right.isoformat()})
    return merge_windows(result)


def enroll(existing: dict[str, dict], current: dict[str, dict], today: date,
           history_start: date | None, now: datetime | None = None) -> dict[str, dict]:
    now = now or datetime.now(UTC)
    result = deepcopy(existing)
    for isin, target in result.items():
        if isin not in current:
            target.update(active=False, state="paused")
    for isin, mapping in current.items():
        mapping_hash = fingerprint({k: mapping.get(k) for k in ("codes", "issue_date", "blocked_reason")})
        target = result.get(isin)
        if target is None:
            target = {
                "history_from": history_start, "history_to": today - timedelta(days=1),
                "pending": [], "generation": 1, "mapping_hash": mapping_hash,
                "active": True, "first_seen_at": now, "last_progress_at": now,
            }
            if history_start and history_start <= target["history_to"]:
                target["pending"] = [{"from": history_start.isoformat(), "to": target["history_to"].isoformat()}]
            result[isin] = target
        else:
            if not target["active"]:
                last_active = target.get("last_active_date")
                if last_active and last_active < today:
                    target["pending"].append({"from": last_active.isoformat(), "to": (today - timedelta(days=1)).isoformat()})
                    target["history_to"] = max(target["history_to"], today - timedelta(days=1))
                target["generation"] += 1
            if history_start and (target["history_from"] is None or history_start < target["history_from"]):
                previous_start = target["history_from"]
                if previous_start is None:
                    target["history_to"] = today - timedelta(days=1)
                end = previous_start - timedelta(days=1) if previous_start else target["history_to"]
                if history_start <= end:
                    target["pending"].append({"from": history_start.isoformat(), "to": end.isoformat()})
                target["history_from"] = history_start
                target["generation"] += 1
            if target["mapping_hash"] != mapping_hash:
                target["generation"] += 1
                target["history_to"] = today - timedelta(days=1)
                if target["history_from"]:
                    target["pending"] = [{"from": target["history_from"].isoformat(), "to": target["history_to"].isoformat()}]
        reason = mapping.get("blocked_reason") or ("history_start_unconfigured" if target["history_from"] is None else None)
        target.update(
            active=True, mapping=mapping, mapping_hash=mapping_hash,
            last_seen_at=now, last_active_date=today, blocked_reason=reason,
            pending=merge_windows(target["pending"]),
        )
        target["state"] = "blocked" if reason else "pending" if target["pending"] else "complete"
    return result


def recent_window(source: str, today: date) -> tuple[date, date]:
    if source == "nse_debt_announcements":
        return today - timedelta(days=6), today
    if source == "bse_corporate_actions":
        return today - timedelta(days=30), today + timedelta(days=90)
    raise ValueError("unknown_source")


def history_work(targets: dict[str, dict], window_days: int = 14) -> Iterator[tuple[date, date, set[str]]]:
    if window_days < 1:
        raise ValueError("window_days must be positive")
    remaining = {
        isin: deepcopy(t["pending"]) for isin, t in targets.items()
        if t["active"] and not t.get("blocked_reason") and t["pending"]
    }
    while remaining:
        oldest = min(targets[i].get("last_progress_at") or datetime.min.replace(tzinfo=UTC) for i in remaining)
        cohort = {i: ws for i, ws in remaining.items()
                  if (targets[i].get("last_progress_at") or datetime.min.replace(tzinfo=UTC)) == oldest}
        end = max(date.fromisoformat(w["to"]) for ws in cohort.values() for w in ws)
        latest = [w for ws in cohort.values() for w in ws if date.fromisoformat(w["to"]) == end]
        start = max(end - timedelta(days=window_days - 1), min(date.fromisoformat(w["from"]) for w in latest))
        selected = {isin for isin, windows in remaining.items() if any(
            date.fromisoformat(w["from"]) <= end and date.fromisoformat(w["to"]) >= start for w in windows
        )}
        yield start, end, selected
        for isin in selected:
            remaining[isin] = subtract_window(remaining[isin], start, end)
        remaining = {isin: windows for isin, windows in remaining.items() if windows}


@dataclass
class Scope:
    isins: set[str]
    bse_mappings: dict[str, set[str]]
    issue_dates: dict[str, date] = field(default_factory=dict)
    evidence: dict[str, list[dict]] = field(default_factory=dict)

    def mappings(self, source: str) -> dict[str, dict]:
        by_isin: dict[str, list[str]] = defaultdict(list)
        if source == SOURCES[1]:
            for code, matches in self.bse_mappings.items():
                if len(matches) == 1:
                    by_isin[next(iter(matches))].append(code)
        result = {}
        for isin in sorted(self.isins):
            codes = sorted(by_isin.get(isin, []))
            result[isin] = {
                "codes": codes if source == SOURCES[1] else [],
                "issue_date": self.issue_dates[isin].isoformat() if source == SOURCES[1] and isin in self.issue_dates else None,
                "blocked_reason": "no_verified_scrip" if source == SOURCES[1] and not codes else None,
            }
        return result


def load_scope() -> Scope:
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT b.isin, b.status, b.bse_scrip_code, t.allotment_date "
            "FROM bonds b LEFT JOIN bond_terms t ON t.isin = b.isin"
        ).fetchall()
        events = conn.execute(
            "SELECT id, isin, old_value, new_value, event_date FROM bond_events "
            "WHERE field = 'bse_scrip_code' AND event_type = 'identifier_change'"
        ).fetchall()
    current = {r["isin"] for r in rows if r["status"] == "listed" and len(r["isin"]) == 12
               and r["isin"].startswith("INE") and r["isin"][7:9] in {"07", "08", "09"}}
    codes: dict[str, set[str]] = defaultdict(set)
    evidence: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        code = str(row["bse_scrip_code"] or "").strip()
        if code.isdecimal():
            code = str(int(code))
            codes[code].add(row["isin"])
            evidence[code].append({"table": "bonds", "isin": row["isin"], "scrip_code": code})
    for event in events:
        for value in (event["old_value"], event["new_value"]):
            if value and value.isdecimal():
                code = str(int(value))
                codes[code].add(event["isin"])
                evidence[code].append({"table": "bond_events", "id": event["id"], "isin": event["isin"],
                                       "event_date": event["event_date"].isoformat()})
    return Scope(current, dict(codes), {r["isin"]: r["allotment_date"] for r in rows
                                      if r["isin"] in current and r["allotment_date"]}, dict(evidence))


def windows(start: date, end: date, days: int = 14) -> Iterator[tuple[date, date]]:
    if days < 1:
        raise ValueError("window_days must be positive")
    while end >= start:
        left = max(start, end - timedelta(days=days - 1))
        yield left, end
        end = left - timedelta(days=1)


def extract_metadata(source: str, native: dict) -> dict:
    return {**metadata(source, native), "extractor_version": EXTRACTOR_VERSION}


@dataclass
class Budget:
    remaining: int


class Collector:
    def __init__(self, store: Any, client: Any, scope_provider: Callable[[], Scope] = load_scope,
                 sleep: Callable[[float], None] = time_sleep):
        self.store = store
        self.client = client
        self.scope_provider = scope_provider
        self.sleep = sleep
        self.stopped: set[str] = set()

    def _persist(self, source: str, targets: dict[str, dict]) -> None:
        self.store.save_targets(source, targets)

    def _scope_tokens(self, targets: dict[str, dict], isins: set[str]) -> dict:
        return {isin: [targets[isin]["mapping_hash"], targets[isin]["generation"]] for isin in sorted(isins)}

    def _request_spec(self, request: Request, tokens: dict, kind: str,
                      obligation_id: int | None = None, parent_chain: list[dict] | None = None,
                      parent: int | None = None) -> dict:
        return {
            "endpoint": request.url, "request_params": request.params, "axis": request.axis,
            "request_key": fingerprint([request.source, request.params, tokens, kind, EXTRACTOR_VERSION,
                                        obligation_id if parent_chain else None]),
            "scope": tokens, "kind": kind, "attempt": 0, "extractor_version": EXTRACTOR_VERSION,
            "obligation_id": obligation_id, "parent_chain": parent_chain or [], "parent_fetch_id": parent,
        }

    def _queue(self, run_id: int, request: Request, spec: dict) -> dict:
        fetch_id = self.store.queue_fetch(run_id, request.source, request.date_from, request.date_to, spec)
        return {"id": fetch_id, "source": request.source, "window_from": request.date_from,
                "window_to": request.date_to, "spec": spec, "status": "pending"}

    def _resolve(self, source: str, spec: dict) -> None:
        if spec.get("request_key"):
            self.store.resolve_request(source, spec["request_key"])
        for parent in reversed(spec.get("parent_chain", [])):
            if not all(self.store.completed(source, key) for key in parent["child_keys"]):
                break
            self.store.resolve_request(source, parent["request_key"])

    def _children(self, run_id: int, fetch: dict) -> list[dict]:
        source, spec = fetch["source"], fetch["spec"]
        start, end = fetch["window_from"], fetch["window_to"]
        mid = start + (end - start) // 2
        obligation_id = spec.get("obligation_id") or fetch["id"]
        requests = [Request(source, mid + timedelta(days=1), end), Request(source, start, mid)]
        chain = spec.get("parent_chain", []) + [{"request_key": spec["request_key"], "child_keys": []}]
        specs = [self._request_spec(request, spec["scope"], spec["kind"], obligation_id, chain, fetch["id"])
                 for request in requests]
        chain[-1]["child_keys"] = [child["request_key"] for child in specs]
        children = []
        for request, child in zip(requests, specs, strict=True):
            if not self.store.completed(source, child["request_key"]):
                children.append(self._queue(run_id, request, child))
        if not children:
            self._resolve(source, spec)
        return children

    def _apply(self, source: str, fetch: dict, targets: dict[str, dict]) -> None:
        changed = {}
        for isin, token in fetch["spec"].get("scope", {}).items():
            target = targets.get(isin)
            if target is None or not target["active"]:
                continue
            if token != [target["mapping_hash"], target["generation"]]:
                continue
            pending = target["pending"] if target.get("blocked_reason") else subtract_window(target["pending"], fetch["window_from"], fetch["window_to"])
            recent = subtract_window(target.get("recent_pending", []), fetch["window_from"], fetch["window_to"])
            if pending != target["pending"] or recent != target.get("recent_pending", []):
                target.update(pending=pending, recent_pending=recent,
                              state="blocked" if target.get("blocked_reason") else "partial" if pending else "complete",
                              last_progress_at=datetime.now(UTC))
                changed[isin] = target
        if changed:
            self._persist(source, changed)
        self._resolve(source, fetch["spec"])
        self.store.mark_coverage(fetch["id"])

    def _recover(self, source: str, targets: dict[str, dict]) -> list[str]:
        errors = []
        for fetch in self.store.pending_fetches(source=source)[:20]:
            try:
                self.store.index_fetch(fetch["id"], extract_metadata)
            except Exception as exc:
                errors.append("index_" + type(exc).__name__)
        for fetch in self.store.coverage_fetches(source)[:20]:
            self._apply(source, fetch, targets)
        return errors

    def _send(self, run_id: int, request: Request, targets: dict[str, dict], selected: set[str],
              kind: str, budget: Budget, force: bool = False, parent: int | None = None,
              queued: dict | None = None) -> bool:
        if queued is None:
            initial = self._request_spec(request, self._scope_tokens(targets, selected), kind, parent=parent)
            if not force and self.store.completed(request.source, initial["request_key"]):
                return True
            self._queue(run_id, request, initial)
            queued = next(f for f in self.store.unfinished(request.source, kind)
                          if f["spec"]["request_key"] == initial["request_key"])
        assert queued is not None
        key = queued["spec"]["request_key"]
        if self.store.completed(request.source, key):
            return True
        if queued["status"] == "split":
            self._children(run_id, queued)
            return bool(self.store.completed(request.source, key))
        for attempt in range(1, 4):
            if budget.remaining <= 0 or request.source in self.stopped:
                return False
            spec = {**queued["spec"], "attempt": attempt,
                    "obligation_id": queued["spec"].get("obligation_id") or queued["id"]}
            fetch_id = self.store.start_fetch(run_id, request.source, request.date_from, request.date_to, spec)
            meta: dict[str, Any] = {"fetch_status": "failed", "native_count": 0}
            budget.remaining -= 1
            self.sleep(0.3)
            try:
                response = self.client.get(request)
            except Exception as exc:
                meta["error"] = "transport_" + type(exc).__name__
                self.store.save_response(fetch_id, meta, [])
                return False
            meta.update(http_status=response.status_code, content_type=response.content_type,
                        final_url=response.final_url, body_size=len(response.body),
                        body_sha256=hashlib.sha256(response.body).hexdigest() if not response.error else None)
            split = False
            if response.status_code == 403:
                self.stopped.add(request.source)
            if response.error or response.status_code != 200:
                meta["error"] = response.error or f"http_{response.status_code}"
                split = response.error == "response_too_large"
                retryable = response.error in {"timeout", "connection_error", "transport_error", "incomplete_body"} or response.status_code in {429, 500, 502, 503, 504}
                if not split:
                    self.store.save_response(fetch_id, meta, [])
                    if retryable and attempt < 3 and budget.remaining > 0:
                        wait = response.retry_after if response.retry_after is not None else 2 ** attempt
                        if wait > 60:
                            self.stopped.add(request.source)
                            return False
                        self.sleep(max(wait, 0))
                        continue
                    if response.status_code == 429:
                        self.stopped.add(request.source)
                    return False
            else:
                try:
                    native = decode(request, response.body)
                    scope = self.scope_provider()
                    mappings = scope.mappings(request.source)
                    if any(isin not in mappings or fingerprint({k: mappings[isin].get(k) for k in ("codes", "issue_date", "blocked_reason")}) != targets[isin]["mapping_hash"] for isin in selected):
                        raise InvalidResponse("scope_changed")
                    retained = []
                    exclusions: Counter[str] = Counter()
                    for record in native:
                        matches, reason = admit(request.source, record, selected, scope.bse_mappings,
                                                scope.issue_dates if request.source == SOURCES[1] else None)
                        if matches:
                            for association in matches:
                                if association["method"] == "bse_master":
                                    code = association["evidence"]["value"]
                                    association["evidence"]["mapping_records"] = scope.evidence.get(code, [])
                            retained.append({"position": record.position, "text": record.text, "associations": matches})
                        else:
                            exclusions[reason or "unmatched_scope"] += 1
                    meta.update(fetch_status="success" if native else "empty", native_count=len(native),
                                exclusion_counts=dict(exclusions))
                except IncompleteResponse as exc:
                    meta["error"] = exc.code
                    split = True
                except InvalidResponse as exc:
                    meta.update(fetch_status="invalid", error=exc.code)
                    self.store.save_response(fetch_id, meta, [])
                    return False
                else:
                    self.store.save_response(fetch_id, meta, retained)
                    try:
                        self.store.index_fetch(fetch_id, extract_metadata)
                    except Exception:
                        return False
                    self._apply(request.source, {"id": fetch_id, "spec": spec,
                                                "window_from": request.date_from, "window_to": request.date_to}, targets)
                    return True
            if split:
                can_split = request.date_from < request.date_to
                meta["fetch_status"] = "split" if can_split else "incomplete"
                self.store.save_response(fetch_id, meta, [])
                if not can_split:
                    return False
                children = self._children(run_id, {"id": fetch_id, "source": request.source, "spec": spec,
                                                  "window_from": request.date_from, "window_to": request.date_to})
                for child in children:
                    self._send(run_id, Request(request.source, child["window_from"], child["window_to"]),
                               targets, selected, kind, budget, queued=child)
                return bool(self.store.completed(request.source, key))
        return False

    def _drain(self, run_id: int, source: str, kind: str, targets: dict[str, dict], selected: set[str],
               budget: Budget, start: date | None = None, end: date | None = None) -> bool:
        pending = self.store.unfinished(source, kind)
        seen = {f["spec"]["request_key"] for f in pending}
        ok = True
        for old in pending:
            if budget.remaining <= 0 or source in self.stopped:
                break
            spec = old["spec"]
            old_key = spec["request_key"]
            if self.store.completed(source, old_key) or old.get("resolved_at"):
                continue
            if any(self.store.completed(source, p["request_key"]) for p in spec.get("parent_chain", [])):
                self.store.resolve_request(source, old_key)
                continue
            active = {i for i in spec.get("scope", {}) if i in targets and targets[i]["active"]
                      and (source != SOURCES[1] or not targets[i]["mapping"].get("blocked_reason"))}
            if not active:
                self.store.resolve_request(source, old_key)
                continue
            if active - selected:
                continue
            if (start and old["window_from"] < start) or (end and old["window_to"] > end):
                continue
            request = Request(source, old["window_from"], old["window_to"])
            tokens = self._scope_tokens(targets, active)
            if tokens != spec.get("scope"):
                replacement = self._request_spec(request, tokens, kind)
                if not self.store.completed(source, replacement["request_key"]):
                    self._queue(run_id, request, replacement)
                    for queued in self.store.unfinished(source, kind):
                        if queued["spec"]["request_key"] not in seen:
                            pending.append(queued)
                            seen.add(queued["spec"]["request_key"])
                self.store.resolve_request(source, old_key)
                continue
            if old["status"] == "split":
                for child in self._children(run_id, old):
                    if child["spec"]["request_key"] not in seen:
                        pending.append(child)
                        seen.add(child["spec"]["request_key"])
                continue
            ok = self._send(run_id, request, targets, active, kind, budget, queued=old) and ok
        return ok

    def _phase(self, source: str, kind: str, today: date, start: date | None, end: date | None,
               request_budget: int, window_days: int, only: set[str] | None, dry_run: bool) -> dict:
        scope = self.scope_provider()
        current = scope.mappings(source)
        existing = self.store.load_targets(source)
        configured = self.store.history_start(source)
        configure_history = kind == "backfill" and start is not None and only is None
        history_start = min(start, configured) if configure_history and configured else start if configure_history else configured
        targets = enroll(existing, current, today, history_start)
        if kind == "backfill" and only is not None:
            subset = enroll({i: t for i, t in existing.items() if i in only},
                            {i: m for i, m in current.items() if i in only}, today, start)
            targets.update(subset)
        selected = set(current) if only is None else set(current) & only
        if source == SOURCES[1]:
            selected = {isin for isin in selected if not current[isin].get("blocked_reason")}
        for isin, target in targets.items():
            target["last_active_date"] = existing.get(isin, {}).get("last_active_date")
            if kind == "backfill" and start and end and isin in selected and end > target["history_to"]:
                target["pending"] = merge_windows(target["pending"] + [
                    {"from": (target["history_to"] + timedelta(days=1)).isoformat(), "to": end.isoformat()},
                ])
                target["history_to"] = end
                target["generation"] += 1
                target["state"] = "blocked" if target.get("blocked_reason") else "pending"
        if kind == "recent":
            left, right = recent_window(source, today)
            for isin in selected:
                previous = existing.get(isin, {})
                last_seen = previous.get("last_active_date") or previous.get("history_to")
                earliest = min(left, last_seen) if last_seen else left
                targets[isin]["recent_pending"] = merge_windows(targets[isin].get("recent_pending", []) + [
                    {"from": earliest.isoformat(), "to": right.isoformat()},
                ])
                targets[isin]["last_active_date"] = today
        if dry_run:
            return {"ok": True, "targets": len(selected), "new_targets": len(set(current) - set(existing)),
                    "history_states": dict(Counter(t["state"] for t in targets.values())),
                    "range": [d.isoformat() for d in recent_window(source, today)] if kind == "recent" else [str(start), str(end)]}
        self._persist(source, targets)
        errors = self._recover(source, targets)
        if kind == "recent":
            left, right = recent_window(source, today)
            for isin in selected:
                targets[isin]["recent_pending"] = merge_windows(targets[isin].get("recent_pending", []) + [
                    {"from": left.isoformat(), "to": right.isoformat()},
                ])
            self._persist(source, {i: targets[i] for i in selected})
        run_id = self.store.start_run(source, kind, {"today": today.isoformat(), "from": str(start), "to": str(end),
                                                    "scope": self._scope_tokens(targets, selected), "window_days": window_days,
                                                    "configure_history": configure_history,
                                                    "history_start": history_start.isoformat() if configure_history and history_start else None})
        budget = Budget(request_budget)
        ok = not errors
        try:
            if kind in {"recent", "refetch"}:
                pending = ([w for i in selected for w in targets[i].get("recent_pending", [])]
                           if kind == "recent" else [{"from": str(start), "to": str(end)}])
                planned = [pair for w in merge_windows(pending)
                           for pair in windows(date.fromisoformat(w["from"]), date.fromisoformat(w["to"]), window_days)]
                planned.sort(key=lambda pair: 0 if pair[0] <= today <= pair[1] else min(abs((pair[0] - today).days), abs((pair[1] - today).days)))
                tokens = self._scope_tokens(targets, selected)
                for a, b in planned:
                    if not selected:
                        break
                    request = Request(source, a, b)
                    self._queue(run_id, request, self._request_spec(request, tokens, kind))
                ok = self._drain(run_id, source, kind, targets, selected, budget) and ok
                if self.store.unfinished(source, kind):
                    ok = False
                    errors.append("pending_requests")
            else:
                ok = self._drain(run_id, source, "history", targets, selected, budget, start, end) and ok
                eligible = {i: deepcopy(t) for i, t in targets.items() if i in selected}
                for queued in self.store.unfinished(source, "history"):
                    for isin, token in queued["spec"].get("scope", {}).items():
                        if isin in eligible and token == [eligible[isin]["mapping_hash"], eligible[isin]["generation"]]:
                            eligible[isin]["pending"] = subtract_window(eligible[isin]["pending"],
                                                                       queued["window_from"], queued["window_to"])
                for a, b, isins in history_work(eligible, window_days):
                    if budget.remaining <= 0 or source in self.stopped:
                        break
                    if start:
                        a = max(a, start)
                    if end:
                        b = min(b, end)
                    if a > b:
                        continue
                    request = Request(source, a, b)
                    spec = self._request_spec(request, self._scope_tokens(targets, isins), "history")
                    if self.store.completed(source, spec["request_key"]):
                        continue
                    queued = self._queue(run_id, request, spec)
                    ok = self._send(run_id, request, targets, isins, "history", budget, queued=queued) and ok
        except Exception as exc:
            errors.append(type(exc).__name__)
            ok = False
        summary = {"ok": ok, "requests": request_budget - budget.remaining, "errors": errors,
                   "target_states": dict(Counter(t["state"] for t in targets.values()))}
        self.store.finish_run(run_id, ok, summary)
        return summary

    def capture(self, sources: list[str], mode: str = "recent", date_from: date | None = None,
                date_to: date | None = None, today: date | None = None, only: set[str] | None = None,
                request_budget: int = 100, history_budget: int = 10, window_days: int = 14,
                dry_run: bool = False) -> dict:
        from zoneinfo import ZoneInfo

        today = today or datetime.now(ZoneInfo("Asia/Kolkata")).date()
        if not sources or any(s not in SOURCES for s in sources) or mode not in {"recent", "backfill", "refetch"}:
            raise ValueError("invalid_source_or_mode")
        if window_days < 1 or request_budget < 1 or history_budget < 0:
            raise ValueError("invalid_request_budget")
        if mode != "recent" and (date_from is None or date_to is None or date_from > date_to):
            raise ValueError("explicit_valid_range_required")
        if mode == "recent" and (date_from is not None or date_to is not None):
            raise ValueError("recent_mode_uses_native_rolling_windows")
        self.stopped.clear()
        results: dict[str, Any] = {"ok": True, "dry_run": dry_run, "sources": {}}
        phases = [mode, "queued_history"] if mode == "recent" and history_budget else [mode]
        for phase in phases:
            for source in dict.fromkeys(sources):
                kind = "backfill" if phase == "queued_history" else phase
                budget = history_budget if phase == "queued_history" else request_budget
                try:
                    if dry_run:
                        result = self._phase(source, kind, today, date_from, date_to, budget, window_days, only, True)
                    else:
                        with self.store.lock(source) as acquired:
                            result = (self._phase(source, kind, today, date_from, date_to, budget, window_days, only, False)
                                      if acquired else {"ok": False, "errors": ["source_locked"]})
                except Exception as exc:
                    result = {"ok": False, "errors": [type(exc).__name__]}
                results["sources"].setdefault(source, {})[phase] = result
                results["ok"] = results["ok"] and result["ok"]
        return results

    def replay(self, sources: list[str], run_id: int | None = None, reindex: bool = False) -> dict:
        results: dict[str, Any] = {"ok": True, "indexed": 0, "errors": []}
        for source in sources:
            with self.store.lock(source) as acquired:
                if not acquired:
                    results["ok"] = False
                    results["errors"].append("source_locked")
                    continue
                for fetch in self.store.pending_fetches(source, run_id, reindex=reindex):
                    try:
                        results["indexed"] += self.store.index_fetch(fetch["id"], extract_metadata)
                    except Exception as exc:
                        results["ok"] = False
                        results["errors"].append(type(exc).__name__)
                targets = self.store.load_targets(source)
                for fetch in self.store.coverage_fetches(source):
                    self._apply(source, fetch, targets)
        return results


def capture_health(snapshot: dict, now: datetime) -> list[str]:
    problems = []
    for source, state in snapshot.items():
        last = state.get("last_recent_success")
        latest = state.get("latest_recent") or {}
        if last is None or now - last > timedelta(hours=18):
            problems.append(f"corporate actions/{source}: recent capture stale or not started")
        if latest.get("state") == "failed":
            problems.append(f"corporate actions/{source}: latest recent capture failed")
        for key, label in (("pending_index", "fetches awaiting index"), ("stuck_runs", "stuck runs"),
                           ("unfinished_recent", "unresolved recent requests")):
            if state.get(key):
                problems.append(f"corporate actions/{source}: {state[key]} {label}")
    return problems
