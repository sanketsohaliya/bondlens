import gzip
import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, suppress
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import Connection, sql
from psycopg.errors import UndefinedTable
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from backend import db

Connect = Callable[[], AbstractContextManager[Connection[DictRow]]]
Extractor = Callable[[str, dict], dict]

_META_KEYS = frozenset({
    "http_status", "status_code", "content_type", "final_url", "body_sha256",
    "body_size", "body_size_bytes", "size_bytes", "fetch_status", "native_count",
    "exclusion_counts", "excluded_count", "error", "error_code", "cache_headers",
    "headers",
})
_CACHE_HEADERS = frozenset({"etag", "last-modified", "cache-control", "expires", "date", "retry-after"})
_FETCH_COLUMNS = """
    f.id, f.run_id, f.source, r.mode, f.window_from, f.window_to,
    f.started_at, f.finished_at, f.status, f.index_status, f.indexed_at,
    f.index_error, f.spec, f.meta, f.accepted_count, f.coverage_applied_at, f.resolved_at
"""
_COMPLETED = """
    ((c.status IN ('success', 'empty') AND c.index_status = 'indexed')
     OR (c.status = 'split' AND c.resolved_at IS NOT NULL))
"""
_UNRESOLVED = f"""
    f.spec ->> 'request_key' IS NOT NULL
    AND f.status IN ('pending', 'running', 'failed', 'invalid', 'incomplete', 'split')
    AND f.resolved_at IS NULL
    AND NOT EXISTS (
        SELECT 1 FROM corporate_action_fetches c
        WHERE c.source = f.source AND c.spec ->> 'request_key' = f.spec ->> 'request_key'
        AND (c.started_at, c.id) > (f.started_at, f.id)
        AND {_COMPLETED}
    )
"""
_UNFINISHED = f"f.source = %s AND f.spec ->> 'kind' = %s AND {_UNRESOLVED}"


class Store:
    def __init__(self, connect: Connect = db._conn,
                 lock_connect: Callable[[], Connection[Any]] | None = None):
        self.connect = connect
        self.lock_connect = lock_connect

    @contextmanager
    def _connection(self) -> Iterator[Connection[DictRow]]:
        try:
            with self.connect() as conn, conn.transaction():
                yield conn
        except UndefinedTable as exc:
            raise RuntimeError("Corporate action storage requires migration 0018_corporate_actions.sql") from exc

    def start_run(self, source: str, mode: str, spec: dict) -> int:
        with self._connection() as conn:
            row = conn.execute(
                "INSERT INTO corporate_action_runs (source, mode, spec) "
                "VALUES (%s, %s, %s) RETURNING id",
                (source, mode, Jsonb(spec)),
            ).fetchone()
        assert row is not None
        return row["id"]

    def finish_run(self, run_id: int, ok: bool, summary: dict) -> None:
        with self._connection() as conn:
            result = conn.execute(
                "UPDATE corporate_action_runs SET finished_at = now(), ok = %s, summary = %s "
                "WHERE id = %s", (ok, Jsonb(summary), run_id),
            )
            if result.rowcount != 1:
                raise ValueError("Unknown corporate action run")

    def start_fetch(self, run_id: int, source: str, date_from: date,
                    date_to: date, spec: dict) -> int:
        if date_to < date_from:
            raise ValueError("Fetch window ends before it starts")
        with self._connection() as conn:
            row = conn.execute(
                "INSERT INTO corporate_action_fetches (run_id, source, window_from, window_to, spec) "
                "SELECT id, source, %s, %s, %s FROM corporate_action_runs "
                "WHERE id = %s AND source = %s RETURNING id",
                (date_from, date_to, Jsonb(spec), run_id, source),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown run or mismatched corporate action source")
        return row["id"]

    def queue_fetch(self, run_id: int, source: str, date_from: date,
                    date_to: date, spec: dict) -> int:
        if date_to < date_from:
            raise ValueError("Fetch window ends before it starts")
        request_key = spec.get("request_key")
        if not isinstance(request_key, str) or not request_key:
            raise ValueError("Queued fetches require a request_key")
        spec = {"kind": "recent", **spec}
        lock_key = int.from_bytes(hashlib.sha256(
            json.dumps(["bondlens:corporate_actions:queue", source, request_key]).encode("utf-8")
        ).digest()[:8], "big", signed=True)
        with self._connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
            row = conn.execute(
                "SELECT f.id FROM corporate_action_fetches f "
                f"WHERE f.source = %s AND f.spec ->> 'request_key' = %s AND {_UNRESOLVED} "
                "ORDER BY f.started_at DESC, f.id DESC LIMIT 1",
                (source, request_key),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "INSERT INTO corporate_action_fetches (run_id, source, window_from, window_to, spec, status) "
                    "SELECT id, source, %s, %s, %s, %s FROM corporate_action_runs "
                    "WHERE id = %s AND source = %s RETURNING id",
                    (date_from, date_to, Jsonb(spec), "pending", run_id, source),
                ).fetchone()
                if row is None:
                    raise ValueError("Unknown run or mismatched corporate action source")
        return row["id"]

    def history_start(self, source: str) -> date | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT spec ->> 'history_start' AS history_start FROM corporate_action_runs "
                "WHERE source = %s AND spec @> %s "
                "ORDER BY run_at DESC, id DESC LIMIT 1",
                (source, Jsonb({"configure_history": True})),
            ).fetchone()
        return date.fromisoformat(row["history_start"]) if row and row["history_start"] is not None else None

    def save_response(self, fetch_id: int, meta: dict, records: list[dict]) -> None:
        safe_meta = {key: value for key, value in meta.items() if key in _META_KEYS}
        for name in ("cache_headers", "headers"):
            if name in safe_meta:
                headers = safe_meta[name]
                safe_meta[name] = {
                    key.lower(): value for key, value in headers.items()
                    if key.lower() in _CACHE_HEADERS
                } if isinstance(headers, dict) else {}
        status = safe_meta.get("fetch_status") or (
            "success" if records or safe_meta.get("native_count", 0) else "empty"
        )
        if status in {"pending", "running"}:
            raise ValueError("A saved response must have a final fetch status")
        safe_meta["fetch_status"] = status
        index_status = "pending" if status in {"success", "empty"} else "not_applicable"
        with self._connection() as conn:
            fetch = conn.execute(
                "SELECT * FROM corporate_action_fetches WHERE id = %s FOR UPDATE", (fetch_id,),
            ).fetchone()
            if fetch is None:
                raise ValueError("Unknown corporate action fetch")
            if fetch["status"] != "running":
                raise ValueError("Fetch evidence is immutable; start a new fetch for another response")
            manifest = []
            record_scope = fetch["spec"].get("record_scope") or ""
            for record in records:
                if not record.get("associations"):
                    raise ValueError("Retained rows require admitted associations")
                data = record["text"].encode("utf-8")
                digest = hashlib.sha256(data).hexdigest()
                raw = conn.execute(
                    "INSERT INTO corporate_action_raw (source, record_scope, row_sha256, content, size_bytes) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (source, record_scope, row_sha256) DO NOTHING RETURNING id",
                    (fetch["source"], record_scope, digest, gzip.compress(data, mtime=0), len(data)),
                ).fetchone()
                if raw is None:
                    raw = conn.execute(
                        "SELECT id FROM corporate_action_raw "
                        "WHERE source = %s AND record_scope = %s AND row_sha256 = %s",
                        (fetch["source"], record_scope, digest),
                    ).fetchone()
                assert raw is not None
                manifest.append({
                    "position": record["position"], "raw_id": raw["id"],
                    "associations": record["associations"],
                })
            conn.execute(
                "UPDATE corporate_action_fetches SET finished_at = now(), status = %s, "
                "index_status = %s, meta = %s, manifest = %s, accepted_count = %s WHERE id = %s",
                (status, index_status, Jsonb(safe_meta), Jsonb(manifest), len(manifest), fetch_id),
            )

    def index_fetch(self, fetch_id: int, extractor: Extractor) -> int:
        try:
            return self._index_fetch(fetch_id, extractor)
        except Exception as exc:
            with suppress(psycopg.Error, RuntimeError), self._connection() as conn:
                conn.execute(
                    "UPDATE corporate_action_fetches SET index_status = %s, index_error = %s "
                    "WHERE id = %s AND status IN ('success', 'empty')",
                    ("failed", type(exc).__name__, fetch_id),
                )
            raise

    def _index_fetch(self, fetch_id: int, extractor: Extractor) -> int:
        with self._connection() as conn:
            fetch = conn.execute(
                "SELECT * FROM corporate_action_fetches WHERE id = %s FOR UPDATE", (fetch_id,),
            ).fetchone()
            if fetch is None or fetch["status"] not in {"success", "empty"}:
                raise ValueError("Only successful retained fetches can be indexed")
            indexed_occurrences = 0
            for occurrence in fetch["manifest"]:
                raw = conn.execute(
                    "SELECT content FROM corporate_action_raw WHERE id = %s AND source = %s",
                    (occurrence["raw_id"], fetch["source"]),
                ).fetchone()
                if raw is None:
                    raise ValueError("Retained evidence is missing")
                text = gzip.decompress(raw["content"]).decode("utf-8")
                native = json.loads(text, parse_float=Decimal)
                if not isinstance(native, dict):
                    raise ValueError("Retained corporate action row must be a JSON object")
                metadata = extractor(fetch["source"], native)
                action = conn.execute(
                    """
                    INSERT INTO corporate_actions (
                        raw_id, source, source_record_id, scrip_code, issuer_name, source_issuer_id,
                        category_raw, headline, announced_at, attachment_urls, source_url, raw,
                        extractor_version, metadata_status, warnings,
                        first_fetch_id, last_fetch_id, first_seen_at, last_seen_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (raw_id) DO UPDATE SET
                        source_record_id = EXCLUDED.source_record_id,
                        scrip_code = EXCLUDED.scrip_code,
                        issuer_name = EXCLUDED.issuer_name,
                        source_issuer_id = EXCLUDED.source_issuer_id,
                        category_raw = EXCLUDED.category_raw,
                        headline = EXCLUDED.headline,
                        announced_at = EXCLUDED.announced_at,
                        attachment_urls = EXCLUDED.attachment_urls,
                        source_url = EXCLUDED.source_url,
                        raw = EXCLUDED.raw,
                        extractor_version = EXCLUDED.extractor_version,
                        metadata_status = EXCLUDED.metadata_status,
                        warnings = EXCLUDED.warnings,
                        first_fetch_id = CASE
                            WHEN (EXCLUDED.first_seen_at, EXCLUDED.first_fetch_id)
                                < (corporate_actions.first_seen_at, corporate_actions.first_fetch_id)
                            THEN EXCLUDED.first_fetch_id ELSE corporate_actions.first_fetch_id END,
                        last_fetch_id = CASE
                            WHEN (EXCLUDED.last_seen_at, EXCLUDED.last_fetch_id)
                                > (corporate_actions.last_seen_at, corporate_actions.last_fetch_id)
                            THEN EXCLUDED.last_fetch_id ELSE corporate_actions.last_fetch_id END,
                        first_seen_at = LEAST(corporate_actions.first_seen_at, EXCLUDED.first_seen_at),
                        last_seen_at = GREATEST(corporate_actions.last_seen_at, EXCLUDED.last_seen_at)
                    RETURNING id
                    """,
                    (
                        occurrence["raw_id"], fetch["source"], metadata.get("source_record_id"),
                        metadata.get("scrip_code", metadata.get("source_scrip_code")),
                        metadata.get("issuer_name"), metadata.get("source_issuer_id"),
                        metadata.get("category_raw"), metadata.get("headline"), metadata.get("announced_at"),
                        Jsonb(metadata.get("attachment_urls", metadata.get("source_links")) or []),
                        metadata.get("source_url"), text,
                        metadata.get("extractor_version") or "", metadata.get("metadata_status") or "ok",
                        Jsonb(metadata.get("warnings") or []), fetch_id, fetch_id,
                        fetch["started_at"], fetch["started_at"],
                    ),
                ).fetchone()
                assert action is not None
                for association in occurrence["associations"]:
                    conn.execute(
                        "INSERT INTO corporate_action_isins (action_id, isin, method, evidence, extractor_version) "
                        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (action_id, isin, method) DO NOTHING",
                        (action["id"], association["isin"], association["method"],
                         Jsonb(association.get("evidence", {})), metadata.get("extractor_version") or ""),
                    )
                indexed_occurrences += 1
            conn.execute(
                "UPDATE corporate_action_fetches SET index_status = %s, indexed_at = now(), "
                "index_error = NULL WHERE id = %s", ("indexed", fetch_id),
            )
        return indexed_occurrences

    def pending_fetches(self, source: str | None = None,
                        run_id: int | None = None, reindex: bool = False) -> list[dict]:
        with self._connection() as conn:
            return conn.execute(
                f"SELECT {_FETCH_COLUMNS} FROM corporate_action_fetches f "
                "JOIN corporate_action_runs r ON r.id = f.run_id "
                "WHERE f.status IN ('success', 'empty') AND (%s OR f.index_status <> 'indexed') "
                "AND (%s::text IS NULL OR f.source = %s) "
                "AND (%s::bigint IS NULL OR f.run_id = %s) ORDER BY f.started_at, f.id",
                (reindex, source, source, run_id, run_id),
            ).fetchall()

    def fetches(self, source: str) -> list[dict]:
        with self._connection() as conn:
            return conn.execute(
                f"SELECT {_FETCH_COLUMNS} FROM corporate_action_fetches f "
                "JOIN corporate_action_runs r ON r.id = f.run_id "
                "WHERE f.source = %s ORDER BY f.started_at, f.id", (source,),
            ).fetchall()

    def coverage_fetches(self, source: str) -> list[dict]:
        with self._connection() as conn:
            return conn.execute(
                f"SELECT {_FETCH_COLUMNS} FROM corporate_action_fetches f "
                "JOIN corporate_action_runs r ON r.id = f.run_id "
                "WHERE f.source = %s AND f.status IN ('success', 'empty') "
                "AND f.index_status = 'indexed' AND f.coverage_applied_at IS NULL "
                "ORDER BY f.started_at, f.id", (source,),
            ).fetchall()

    def mark_coverage(self, fetch_id: int) -> None:
        with self._connection() as conn:
            result = conn.execute(
                "UPDATE corporate_action_fetches "
                "SET coverage_applied_at = COALESCE(coverage_applied_at, now()) "
                "WHERE id = %s AND status IN ('success', 'empty') AND index_status = 'indexed'",
                (fetch_id,),
            )
            if result.rowcount != 1:
                raise ValueError("Coverage requires an indexed successful fetch")

    def unfinished(self, source: str, kind: str = "recent") -> list[dict]:
        with self._connection() as conn:
            return conn.execute(
                f"SELECT * FROM (SELECT DISTINCT ON (f.spec ->> 'request_key') {_FETCH_COLUMNS} "
                "FROM corporate_action_fetches f JOIN corporate_action_runs r ON r.id = f.run_id "
                f"WHERE {_UNFINISHED} "
                "ORDER BY f.spec ->> 'request_key', f.started_at DESC, f.id DESC) latest "
                "ORDER BY started_at, id",
                (source, kind),
            ).fetchall()

    def completed(self, source: str, request_key: str) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT {_COMPLETED} AS completed FROM corporate_action_fetches c "
                "WHERE c.source = %s AND c.spec ->> 'request_key' = %s "
                "ORDER BY c.started_at DESC, c.id DESC LIMIT 1",
                (source, request_key),
            ).fetchone()
        return bool(row and row["completed"])

    def resolve_request(self, source: str, request_key: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE corporate_action_fetches SET resolved_at = now() "
                "WHERE source = %s AND spec ->> 'request_key' = %s AND resolved_at IS NULL",
                (source, request_key),
            )

    def load_targets(self, source: str) -> dict[str, dict]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM corporate_action_targets WHERE source = %s ORDER BY isin", (source,),
            ).fetchall()
        return {row["isin"]: row for row in rows}

    def save_target(self, source: str, isin: str, data: dict) -> None:
        self.save_targets(source, {isin: data})

    def save_targets(self, source: str, targets: dict[str, dict]) -> None:
        if not targets:
            return
        allowed = {
            "mapping_hash", "mapping", "active", "first_seen_at", "last_seen_at",
            "last_progress_at", "history_from", "history_to", "pending", "state",
            "blocked_reason", "generation", "last_active_date", "recent_pending",
        }
        groups: dict[tuple[str, ...], list[tuple]] = {}
        for isin, data in targets.items():
            fields = tuple(sorted(allowed.intersection(data)))
            values = tuple(Jsonb(data[key]) if key in {"mapping", "pending", "recent_pending"} else data[key] for key in fields)
            groups.setdefault(fields, []).append((source, isin, *values))
        with self._connection() as conn, conn.cursor() as cur:
            for fields, rows in groups.items():
                columns = ("source", "isin", *fields)
                updates = [key for key in fields if key != "first_seen_at"]
                conflict = sql.SQL("DO UPDATE SET {} ").format(sql.SQL(", ").join(
                    sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(key), sql.Identifier(key))
                    for key in updates
                )) if updates else sql.SQL("DO NOTHING")
                query = sql.SQL(
                    "INSERT INTO corporate_action_targets ({}) VALUES ({}) ON CONFLICT (source, isin) {}"
                ).format(
                    sql.SQL(", ").join(map(sql.Identifier, columns)),
                    sql.SQL(", ").join(sql.Placeholder() for _ in columns), conflict,
                )
                cur.executemany(query, rows)

    def snapshot(self) -> dict:
        with self._connection() as conn:
            sources = conn.execute(
                "SELECT source FROM corporate_action_runs UNION SELECT source FROM corporate_action_raw "
                "UNION SELECT source FROM corporate_action_targets UNION SELECT %s UNION SELECT %s",
                ("nse_debt_announcements", "bse_corporate_actions"),
            ).fetchall()
            result = {}
            for source_row in sources:
                source = source_row["source"]
                runs = conn.execute(
                    "SELECT max(finished_at) FILTER (WHERE mode = %s AND ok IS TRUE) AS last_recent_success, "
                    "count(*) FILTER (WHERE finished_at IS NULL) AS running_runs, "
                    "count(*) FILTER (WHERE finished_at IS NULL AND run_at < now() - %s::interval) AS stuck_runs "
                    "FROM corporate_action_runs WHERE source = %s", ("recent", "3 hours", source),
                ).fetchone()
                latest = conn.execute(
                    "SELECT id, run_at, finished_at, ok FROM corporate_action_runs "
                    "WHERE source = %s AND mode = %s ORDER BY run_at DESC, id DESC LIMIT 1",
                    (source, "recent"),
                ).fetchone()
                if latest is not None:
                    latest["state"] = "running" if latest["finished_at"] is None else (
                        "success" if latest["ok"] else "failed"
                    )
                pending = conn.execute(
                    "SELECT count(*) AS pending_index FROM corporate_action_fetches "
                    "WHERE source = %s AND status IN ('success', 'empty') AND index_status <> 'indexed'",
                    (source,),
                ).fetchone()
                raw = conn.execute(
                    "SELECT count(*) AS raw_rows, COALESCE(sum(size_bytes), 0)::bigint AS raw_bytes, "
                    "COALESCE(sum(octet_length(content)), 0)::bigint AS compressed_bytes "
                    "FROM corporate_action_raw WHERE source = %s", (source,),
                ).fetchone()
                actions = conn.execute(
                    "SELECT count(*) AS rows FROM corporate_actions WHERE source = %s", (source,),
                ).fetchone()
                states = conn.execute(
                    "SELECT state, count(*) AS n FROM corporate_action_targets WHERE source = %s GROUP BY state",
                    (source,),
                ).fetchall()
                progress = conn.execute(
                    "SELECT COALESCE(sum(jsonb_array_length(pending)), 0)::bigint AS remaining_history_windows, "
                    "COALESCE(sum(jsonb_array_length(recent_pending)), 0)::bigint AS remaining_recent_windows, "
                    "min(last_progress_at) FILTER (WHERE state IN ('pending', 'partial')) AS oldest_progress_at "
                    "FROM corporate_action_targets WHERE source = %s", (source,),
                ).fetchone()
                unfinished = conn.execute(
                    "SELECT count(DISTINCT f.spec ->> 'request_key') AS unfinished_recent "
                    f"FROM corporate_action_fetches f WHERE {_UNFINISHED}", (source, "recent"),
                ).fetchone()
                blocked = conn.execute(
                    "SELECT blocked_reason, count(*) AS n FROM corporate_action_targets "
                    "WHERE source = %s AND state = %s AND blocked_reason IS NOT NULL GROUP BY blocked_reason",
                    (source, "blocked"),
                ).fetchall()
                target_states = dict.fromkeys(("pending", "partial", "complete", "blocked", "paused"), 0)
                target_states.update({row["state"]: row["n"] for row in states})
                result[source] = {
                    **(runs or {}), "latest_recent": latest, **(pending or {}),
                    **(raw or {}), **(actions or {}), "target_states": target_states, **(progress or {}),
                    **(unfinished or {}), "blocked_reasons": {row["blocked_reason"]: row["n"] for row in blocked},
                }
        return result

    @contextmanager
    def lock(self, source: str) -> Iterator[bool]:
        key = int.from_bytes(hashlib.sha256(
            ("bondlens:corporate_actions:" + source).encode("utf-8")
        ).digest()[:8], "big", signed=True)
        conn = self.lock_connect() if self.lock_connect else psycopg.connect(
            db.DATABASE_URL, autocommit=True, connect_timeout=10,
        )
        acquired = False
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (key,))
                row = cur.fetchone()
                assert row is not None
                acquired = bool(row["acquired"] if isinstance(row, dict) else row[0])
            yield acquired
        finally:
            try:
                if acquired:
                    conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
            finally:
                conn.close()
