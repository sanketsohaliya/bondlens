"""yield -> price threshold cache for opportunity alerts.

Solving "which dirty price yields 12%" costs a schedule build and a few
dozen yield solves per bond; across the universe that is minutes, which
is fine before the open and useless on a tick. So the pairs every rule
and exit target needs are solved once per valuation date (in a process
pool, pre-open) into price_thresholds, and the watcher only reads them.
Intraday refreshes solve deltas: a new rule yield level, a bond that
joined the universe, or a bond whose terms/cashflows changed since the
row was computed (schedule_version fingerprint).
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from typing import Any

from . import db, opportunities, queries
from .analytics import schedule, valuation

Pair = tuple[str, float]


def needed(rules: list[dict], candidates: dict[int, list[str]],
           targets: list[dict]) -> set[Pair]:
    """Every (isin, ytm) the given rules/targets will compare against."""
    pairs: set[Pair] = set()
    for rule in rules:
        ytm = rule.get("min_ask_ytm")
        if rule.get("enabled", True) and ytm is not None:
            pairs.update((isin, float(ytm)) for isin in candidates.get(rule["id"], []))
    for target in targets:
        ytm = target.get("max_bid_ytm")
        if target.get("enabled", True) and ytm is not None:
            pairs.add((target["isin"], float(ytm)))
    return pairs


def fingerprints(isins: list[str]) -> dict[str, str]:
    """Current schedule_version per ISIN from the raw pricing inputs."""
    terms = queries.terms_for(isins)
    flows = queries.cashflows_for(isins)
    return {isin: opportunities.schedule_fingerprint(terms.get(isin), flows.get(isin, []))
            for isin in isins}


def load(today: date, current: dict[str, str]) -> dict[Pair, float | None]:
    """Cached prices for `today` whose inputs and calculator still match."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT isin, ytm, price, schedule_version, calculation_version "
            "FROM price_thresholds WHERE valuation_date = %s", (today,)).fetchall()
    out: dict[Pair, float | None] = {}
    for r in rows:
        if (r["calculation_version"] == valuation.VERSION
                and current.get(r["isin"]) == r["schedule_version"]):
            out[(r["isin"], float(r["ytm"]))] = float(r["price"]) if r["price"] is not None else None
    return out


def _solve_bond(isin: str, ytms: list[float], terms: dict | None,
                cashflows: list[dict], today: date) -> list[tuple[str, float, float | None]]:
    """Pool worker: one schedule build, one bisection per yield level."""
    sched = schedule.build(isin, cashflows, terms, today=today)
    return [(isin, ytm, opportunities.price_for_yield(sched, terms, ytm, today)) for ytm in ytms]


def solve(pairs: set[Pair], today: date, *, workers: int | None = None,
          executor_cls: Any = ProcessPoolExecutor) -> list[tuple[str, float, float | None]]:
    """Solve the given pairs, grouped per bond so each schedule is built once."""
    if not pairs:
        return []
    by_isin: dict[str, list[float]] = {}
    for isin, ytm in pairs:
        by_isin.setdefault(isin, []).append(ytm)
    isins = sorted(by_isin)
    terms = queries.terms_for(isins)
    flows = queries.cashflows_for(isins)
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    results: list[tuple[str, float, float | None]] = []
    with executor_cls(max_workers=workers) as pool:
        futures = [pool.submit(_solve_bond, isin, sorted(by_isin[isin]), terms.get(isin),
                               flows.get(isin, []), today) for isin in isins]
        for fut in futures:
            results.extend(fut.result())
    return results


def store(rows: list[tuple[str, float, float | None]], today: date,
          current: dict[str, str]) -> None:
    if not rows:
        return
    with db._conn() as conn:
        conn.cursor().executemany(
            "INSERT INTO price_thresholds (isin, ytm, valuation_date, schedule_version, "
            " calculation_version, price, computed_at) VALUES (%s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (isin, ytm, valuation_date) DO UPDATE SET price = EXCLUDED.price, "
            " schedule_version = EXCLUDED.schedule_version, "
            " calculation_version = EXCLUDED.calculation_version, computed_at = now()",
            [(isin, ytm, today, current.get(isin, ""), valuation.VERSION, price)
             for isin, ytm, price in rows])


def ensure(rules: list[dict], candidates: dict[int, list[str]], targets: list[dict],
           today: date, *, workers: int | None = None, executor_cls: Any = ProcessPoolExecutor,
           ) -> tuple[dict[Pair, float | None], int]:
    """Load what is cached and valid, solve the rest, store it. Returns
    (prices for every needed pair, number solved now)."""
    pairs = needed(rules, candidates, targets)
    if not pairs:
        return {}, 0
    isins = sorted({isin for isin, _ in pairs})
    current = fingerprints(isins)
    prices = load(today, current)
    missing = {p for p in pairs if p not in prices}
    solved = solve(missing, today, workers=workers, executor_cls=executor_cls)
    store(solved, today, current)
    for isin, ytm, price in solved:
        prices[(isin, ytm)] = price
    return {p: prices[p] for p in pairs if p in prices}, len(solved)


def prune(keep_from: date) -> int:
    """Drop rows for valuation dates before `keep_from`."""
    with db._conn() as conn:
        cur = conn.execute("DELETE FROM price_thresholds WHERE valuation_date < %s", (keep_from,))
        return cur.rowcount
