import argparse
import json
import re
import sys
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class VerificationError(Exception):
    pass


def ensure(condition, message):
    if not condition:
        raise VerificationError(message)


def fetch(url):
    with urlopen(url, timeout=30) as response:
        return response.read().decode()


def verify_ui(site):
    page = fetch(site)
    bundles = [src for src in re.findall(r'<script[^>]+src="([^"]+)"', page)
               if src.startswith("/assets/") and src.endswith(".js")]
    ensure(bool(bundles), "Frontend module bundle not found")
    bundle = "\n".join(fetch(urljoin(site, src)) for src in bundles)
    for marker in ("Ask AI", "Mark loaded as read", "calculation_version", "expires_at", "Market comparison"):
        ensure(marker in bundle, f"Frontend marker missing: {marker}")
    try:
        fetch(urljoin(site, "/api/auth/me"))
    except HTTPError as exc:
        ensure(exc.code == 401 and "Sign in required" in exc.read().decode(), "Authentication guard check failed")
    else:
        raise VerificationError("Unauthenticated request unexpectedly succeeded")
    print(json.dumps({"public_ui": "verified", "authentication_guard": "verified"}))


def verify_database(wait_migration=False):
    from psycopg import Connection
    from psycopg.rows import DictRow, dict_row

    from backend import app as api_module
    from backend import db, models
    from backend.analytics import compute, valuation

    with Connection[DictRow].connect(db.DATABASE_URL, row_factory=dict_row,
                                    options="-c default_transaction_read_only=on -c statement_timeout=120000") as conn:
        mode = conn.execute("SHOW transaction_read_only").fetchone()
        ensure(mode is not None and mode["transaction_read_only"] == "on", "Verification connection is not read-only")
        if wait_migration:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                found = conn.execute("SELECT 1 FROM public.schema_migrations WHERE filename = %s",
                                     ("0021_valuation_context.sql",)).fetchone()
                if found:
                    print("Migration 0021 is applied")
                    return
                time.sleep(2)
            raise VerificationError("Migration 0021 did not finish")
        conn.rollback()
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")

        @contextmanager
        def connect():
            yield conn

        def without_clock(row):
            value = dict(row)
            value.pop("calculated_at", None)
            return value

        # kite_url is looked up from Kite's instrument list (network) in both
        # bond_detail and compute.build_rows; patch both so verification is
        # offline, and skip the field below: an external listing that may
        # legitimately differ between the stored refresh and this run.
        with patch.object(db, "_conn", connect), \
                patch.object(api_module.kite, "kite_url", return_value=None), \
                patch.object(compute.kite, "kite_url", return_value=None):
            stored = {row["isin"]: dict(row) for row in conn.execute("SELECT * FROM bond_analytics")}
            ensure(bool(stored), "Stored analytics are empty")
            dates = {row["payout_as_of"] for row in stored.values()}
            ensure(len(dates) == 1 and None not in dates, "Analytics snapshot dates are inconsistent")
            as_of = next(iter(dates))
            fresh = {row["isin"]: row for row in compute.build_rows(today=as_of)}
            ensure(stored.keys() == fresh.keys(), "Stored and fresh analytics cover different instruments")
            mismatches = []
            otc_only = 0
            for isin, expected in fresh.items():
                actual = stored[isin]
                ensure((actual.get("valuation") or {}).get("calculation_version") == valuation.VERSION,
                       f"Stale calculation version: {isin}")
                notes = actual["valuation"].get("warnings", [])
                ensure(all(note["severity"] == "info" for note in notes if note["code"] == "otc_difference"),
                       f"OTC comparison is not informational: {isin}")
                if notes and all(note["code"] == "otc_difference" for note in notes):
                    ensure(actual["ytm_reliable"] is True and actual["valuation"]["ytm_reliable"] is True,
                           f"OTC-only comparison still marks yield unreliable: {isin}")
                    otc_only += 1
                for field in compute._COLS:
                    if field in {"computed_at", "kite_url"}:
                        continue
                    left, right = actual[field], expected[field]
                    if field == "valuation":
                        left, right = without_clock(left), without_clock(right)
                    if left != right:
                        mismatches.append((isin, field))
            ensure(not mismatches, f"Stored/fresh mismatches: {len(mismatches)}; first cases: {mismatches[:10]}")
            preferred = ["INE342T07536", "INE2UHF07015", "INE134E07CH9"]
            preferred += [isin for isin, row in fresh.items() if row["ytm_method"] in {"ytc", "estimated"}][:5]
            preferred += [isin for isin, row in fresh.items() if row["ytm"] is not None][:5]
            samples = []
            for isin in list(dict.fromkeys(preferred))[:12]:
                if isin not in fresh:
                    continue
                detail = api_module.bond_detail(isin, price=None)
                models.BondDetail.model_validate(detail)
                ensure(detail["valuation"]["calculation_version"] == valuation.VERSION, f"Detail version mismatch: {isin}")
                if as_of == date.today():
                    ensure(detail["ytm"] == fresh[isin]["ytm"], f"Detail/stored yield mismatch: {isin}")
                if detail["ltp"]:
                    quote = api_module.bond_valuation(isin, price=detail["ltp"])
                    models.YtmAtPrice.model_validate(quote)
                    ensure(quote["as_of"] == detail["valuation"]["as_of"], f"Price valuation date mismatch: {isin}")
                    if detail["valuation"]["ytm_method"] != "approx":
                        ensure(quote["ytm"] == detail["ytm"], f"Price valuation/detail mismatch: {isin}")
                samples.append({"isin": isin, "ytm": detail["ytm"], "method": detail["valuation"]["ytm_method"]})
            ensure(bool(samples), "No representative service valuations could be checked")
            print(json.dumps({"stored_rows": len(stored), "mismatches": len(mismatches),
                              "otc_only_without_caution": otc_only, "snapshot_date": str(as_of), "calculation_version": valuation.VERSION,
                              "service_samples": samples}))
    schema = json.loads(fetch("http://127.0.0.1:8000/openapi.json"))
    ensure("/api/bonds/{isin}/valuation" in schema["paths"], "Running API lacks price valuation route")
    ensure("calculated_at" in schema["components"]["schemas"]["YieldValuation"]["properties"], "Running API lacks valuation metadata")
    print("Running API schema verified")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-migration", action="store_true")
    parser.add_argument("--public-only", action="store_true")
    parser.add_argument("--site", default="https://bondlens.in/")
    args = parser.parse_args()
    try:
        if args.public_only:
            verify_ui(args.site)
        else:
            verify_database(args.wait_migration)
    except VerificationError as exc:
        print(f"Verification failed: {exc}")
        return 1
    except Exception as exc:
        print(f"Verification failed: {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
