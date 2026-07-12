"""End-to-end pipeline test: the REAL scrapers and parsers, run exactly as
cron runs them (subprocesses), against a temporary database.

    .venv/bin/pytest -m e2e -v -s

Sequence (each stage feeds the next):
    1. universe   — live NSE+BSE masters -> bonds
    2. EOD        — last trading day's files -> eod_raw -> eod_trades
    3. NSDL       — 3 traded ISINs -> nsdl_raw -> bond_terms/cashflows/...
    4. analytics  — compute_analytics -> bond_analytics with real YTMs

Failure semantics: unreachable endpoints SKIP loudly (nothing to fix in
code); a 200 response that doesn't parse, or missing DB rows, FAILS
(the contract drifted — our code needs updating).

Everything runs against bondlens_e2e, created from migrations and dropped
afterwards. The live database is never touched.
"""
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest
from psycopg.rows import dict_row

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parent.parent
ADMIN_URL = os.environ.get(
    "BONDLENS_ADMIN_URL",
    "postgresql://bondlens:bondlens@localhost:5432/postgres")
E2E_DB = "bondlens_e2e"
E2E_URL = ADMIN_URL.rsplit("/", 1)[0] + "/" + E2E_DB
SCRIPT_TIMEOUT = 600
NSDL_SAMPLE_SIZE = 10   # ISINs harvested in stage 3 (~15 calls each)

# Stage progress: later stages skip (not fail) when a prerequisite failed.
_done: dict[str, Any] = {}

_NETWORK_ERRORS = ("ConnectionError", "ConnectTimeout", "ReadTimeout",
                   "NameResolutionError", "Max retries exceeded",
                   "RemoteDisconnected", "TimeoutError")


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def temp_db():
    """Create bondlens_e2e from migrations; drop it afterwards."""
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {E2E_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {E2E_DB}")
    try:
        res = run_script(["scripts/migrate.py"])
        assert res.returncode == 0, f"migrate failed:\n{res.stdout}\n{res.stderr}"
        yield
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {E2E_DB} WITH (FORCE)")


def run_script(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a production script exactly as cron does, but against the e2e DB."""
    cmd = ([sys.executable, "-m", args[0]] if args[0].startswith("backend.")
           else [sys.executable, *args])
    print(f"\n$ {' '.join(cmd)}")
    res = subprocess.run(
        cmd, cwd=ROOT, env={**os.environ, "DATABASE_URL": E2E_URL},
        capture_output=True, text=True, timeout=SCRIPT_TIMEOUT)
    tail = (res.stdout + res.stderr).strip().splitlines()[-8:]
    print("\n".join(f"  | {line}" for line in tail))
    return res


def q(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    conn = cast("psycopg.Connection[dict[str, Any]]",
                psycopg.connect(E2E_URL, row_factory=dict_row))  # pyright: ignore[reportArgumentType]
    with conn:
        return conn.execute(sql, params).fetchall()  # type: ignore[arg-type]


def one(sql: str, params: tuple = ()) -> dict[str, Any]:
    rows = q(sql, params)
    assert rows, f"no rows from: {sql}"
    return rows[0]


def skip_if_network_error(res: subprocess.CompletedProcess[str], stage: str):
    output = res.stdout + res.stderr
    if any(marker in output for marker in _NETWORK_ERRORS):
        pytest.skip(f"{stage}: endpoint unreachable (not a code problem) — "
                    f"last output:\n{output.strip()[-500:]}")


def require(stage: str):
    if not _done.get(stage):
        pytest.skip(f"prerequisite stage '{stage}' did not complete")


def last_trading_day() -> date:
    """Most recent weekday strictly before today (today's files may not be
    published yet). Holidays are handled by the absent-vs-malformed check."""
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# --------------------------------------------------------------------------
# Stage 1: universe scrape (backend/universe.py + nse.py + bse.py)
# --------------------------------------------------------------------------

def test_stage1_universe_scrape():
    res = run_script(["backend.universe"])
    skip_if_network_error(res, "universe")
    assert res.returncode == 0, f"universe exited {res.returncode}"

    run = one("SELECT ok, total FROM bond_runs ORDER BY id DESC LIMIT 1")
    assert run["ok"] is True, "bond_runs.ok is not true"
    assert run["total"] > 5000, f"suspiciously small universe: {run['total']}"

    counts = one("""
        SELECT count(*) FILTER (WHERE on_nse) AS nse,
               count(*) FILTER (WHERE on_bse) AS bse,
               count(*) AS total FROM bonds WHERE status = 'listed'""")
    assert counts["nse"] > 1000, f"NSE side too small: {counts['nse']}"
    assert counts["bse"] > 3000, f"BSE side too small: {counts['bse']}"

    raws = q("SELECT source FROM raw_files")
    assert {r["source"] for r in raws} == {"nse_debt_csv", "bse_scrip_master"}
    _done["universe"] = True
    print(f"  universe OK: {counts['total']} listed "
          f"(NSE {counts['nse']} / BSE {counts['bse']})")


# --------------------------------------------------------------------------
# Stage 2: EOD trades (scripts/harvest_eod.py + parse_eod.py)
# --------------------------------------------------------------------------

def test_stage2_eod_scrape_and_parse():
    require("universe")
    day = last_trading_day()
    res = run_script(["scripts/harvest_eod.py",
                      "--from", day.isoformat(), "--to", day.isoformat()])
    skip_if_network_error(res, "eod harvest")
    assert res.returncode == 0

    raw = one("""
        SELECT count(*) FILTER (WHERE http_status = 200) AS fetched,
               count(*) AS total FROM eod_raw""")
    assert raw["total"] >= 3, "harvester recorded nothing at all"
    if raw["fetched"] == 0:
        pytest.skip(f"{day}: no EOD files published (exchange holiday?) — "
                    "misses were recorded correctly")

    res = run_script(["scripts/parse_eod.py"])
    assert res.returncode == 0, "parse_eod crashed"
    trades = one("""
        SELECT count(*) AS n,
               count(*) FILTER (WHERE segment = 'retail') AS retail,
               count(*) FILTER (WHERE close IS NOT NULL OR wap IS NOT NULL
                                OR last_price IS NOT NULL) AS priced
        FROM eod_trades""")
    assert trades["n"] > 50, (
        f"only {trades['n']} trades parsed from a {raw['fetched']}-file "
        "trading day — format drift?")
    assert trades["priced"] == trades["n"], "trades without any price field"
    bad = q("SELECT count(*) AS n FROM eod_trades WHERE close < 0 OR wap < 0")
    assert bad[0]["n"] == 0, "negative prices parsed"
    _done["eod"] = trades["retail"] > 0
    print(f"  eod OK: {trades['n']} trades ({trades['retail']} retail) on {day}")


# --------------------------------------------------------------------------
# Stage 3: NSDL enrichment (scripts/harvest_nsdl.py + parse_nsdl.py)
# --------------------------------------------------------------------------

def test_stage3_nsdl_scrape_and_parse():
    require("universe")
    # Prefer ISINs that actually traded (stage 2) so stage 4 can price them.
    picked = [r["isin"] for r in q("""
        SELECT b.isin FROM bonds b
        JOIN eod_trades t ON t.isin = b.isin AND t.segment = 'retail'
        WHERE b.status = 'listed'
        GROUP BY b.isin ORDER BY sum(t.turnover) DESC NULLS LAST
        LIMIT %s""", (NSDL_SAMPLE_SIZE,))]
    if not picked:  # EOD skipped (holiday) — fall back to dual-listed names
        picked = [r["isin"] for r in q(
            "SELECT isin FROM bonds WHERE status = 'listed' "
            "AND on_nse AND on_bse ORDER BY isin LIMIT %s",
            (NSDL_SAMPLE_SIZE,))]
    assert picked, "no candidate ISINs in scraped universe"
    print(f"  harvesting: {picked}")

    res = run_script(["scripts/harvest_nsdl.py", "--force", "--workers", "2",
                      *[f"--isin={i}" for i in picked]])
    skip_if_network_error(res, "nsdl harvest")
    assert res.returncode == 0

    run = one("SELECT ok, fetched, failed FROM nsdl_runs "
              "ORDER BY id DESC LIMIT 1")
    assert run["ok"] is True, f"nsdl_runs not ok: {run}"
    raw = one("SELECT count(DISTINCT isin) AS isins, count(*) AS rows "
              "FROM nsdl_raw WHERE http_status = 200")
    min_ok = max(2, int(len(picked) * 0.7))  # NSDL misses ~17% of universe
    assert raw["isins"] >= min_ok, (
        f"NSDL returned data for only {raw['isins']}/{len(picked)} ISINs")

    res = run_script(["scripts/parse_nsdl.py"])
    assert res.returncode == 0, "parse_nsdl crashed"

    terms = q("SELECT isin, coupon_rate, face_value FROM bond_terms")
    assert terms, "no bond_terms parsed from fetched NSDL data"
    for t in terms:
        if t["coupon_rate"] is not None:
            assert 0 <= t["coupon_rate"] <= 20, f"absurd coupon: {t}"
        if t["face_value"] is not None:
            assert t["face_value"] > 0, f"non-positive face: {t}"
    flows = one("SELECT count(*) AS n, count(DISTINCT isin) AS isins "
                "FROM bond_cashflows")
    assert flows["n"] >= 1, "no cashflows parsed"
    _done["nsdl"] = [t["isin"] for t in terms]
    print(f"  nsdl OK: terms for {len(terms)} ISINs, "
          f"{flows['n']} cashflows")


# --------------------------------------------------------------------------
# Stage 4: analytics (scripts/compute_analytics.py)
# --------------------------------------------------------------------------

def test_stage4_analytics():
    require("universe")
    require("nsdl")
    res = run_script(["scripts/compute_analytics.py"])
    assert res.returncode == 0, "compute_analytics crashed"

    total = one("SELECT count(*) AS n FROM bond_analytics")["n"]
    listed = one("SELECT count(*) AS n FROM bonds WHERE status='listed'")["n"]
    assert total == listed, f"analytics rows {total} != listed bonds {listed}"

    enriched = q("""
        SELECT isin, schedule_shape, coupon, ytm, ltp FROM bond_analytics
        WHERE isin = ANY(%s)""", (_done["nsdl"],))
    # NSDL has no schedule data for a slice of the universe (~17%, e.g.
    # market-linked debentures) — require a majority, not all.
    shaped = [r for r in enriched if r["schedule_shape"] != "empty"]
    assert len(shaped) >= max(1, len(enriched) // 2), (
        f"most harvested ISINs came out shapeless: {enriched}")
    if _done.get("eod"):
        priced = [r for r in enriched if r["ltp"] is not None]
        assert priced, "traded+parsed ISINs have no price in analytics"
        with_ytm = [r for r in priced if r["ytm"] is not None]
        assert with_ytm, f"no YTM computed for priced ISINs: {priced}"
        for r in with_ytm:
            assert -5 < r["ytm"] < 100, f"absurd YTM: {r}"
        print(f"  analytics OK: {total} rows; end-to-end YTMs: "
              f"{[(r['isin'], r['ytm']) for r in with_ytm]}")
    else:
        print(f"  analytics OK: {total} rows (no prices — EOD was skipped)")
