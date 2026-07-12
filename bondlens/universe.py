"""Daily corporate-bond universe tracker.

Builds the currently-listed corporate-bond universe as the union of the
two exchange listing masters:

  * NSE : DEBT.csv  (Capital Market listed-debt master)
  * BSE : ListofScripData, status Active + Suspended, "Debentures and Bonds"

Only genuine corporate bonds are kept (ISIN "INE" + security-type 07/08/09).
The union is upserted into the v2 database (bonds), day-over-day changes are
logged (bond_events), and each run is summarized (bond_runs).

Run manually:
    python -m bondlens.universe
    python -m bondlens.universe --dry-run          # fetch + report, no DB write
    python -m bondlens.universe --active-only       # exclude BSE Suspended
    python -m bondlens.universe --json              # machine-readable summary

Cron (weekdays + Sat, 05:30 IST -> after NSE's ~03:00 master regen):
    30 5 * * 1-6  cd /home/sanket/code/BondLens && \
        .venv/bin/python -m bondlens.universe >> /var/log/bondlens_universe.log 2>&1
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone

from . import bse, nse

_ISIN = re.compile(r"^IN[A-Z0-9]{10}$")
CORP_TYPES = {"07", "08", "09"}


def _is_corp(isin: str) -> bool:
    return bool(isin and _ISIN.match(isin)
                and isin.startswith("INE") and isin[7:9] in CORP_TYPES)


def build_records(active_only: bool = False):
    """Fetch both masters and return (records, stats, raws).

    records: one merged dict per corporate ISIN in the union.
    stats:   source counts for the run summary.
    raws:    verbatim fetched payloads keyed by source, for archival.
    """
    statuses = ("Active",) if active_only else bse.LISTED_STATUSES

    raw_csv = nse.fetch_debt_csv()
    nse_rows = [r for r in nse.parse_debt_isins(raw_csv)
                if _is_corp(r["isin"])]
    raw_master = bse.fetch_scrip_master_raw(statuses=statuses)
    bse_rows = [r for r in bse.parse_scrip_master(raw_master)
                if _is_corp(r["isin"])]

    nse_by = {r["isin"]: r for r in nse_rows}
    bse_by = {r["isin"]: r for r in bse_rows}
    all_isins = sorted(set(nse_by) | set(bse_by))

    # Store ONLY exchange-specific, non-refetchable data. Descriptive fields
    # (coupon, face value, dates, security type) are intentionally omitted --
    # they are joined from the NSDL cache / derived from the ISIN at read
    # time. issuer_name IS kept: NSDL misses ~17% of the listed universe,
    # so the exchange name is the only source for those bonds.
    records = []
    for isin in all_isins:
        n = nse_by.get(isin) or {}
        b = bse_by.get(isin) or {}
        records.append({
            "isin": isin,
            "on_nse": isin in nse_by,
            "on_bse": isin in bse_by,
            "bse_status": b.get("bse_status"),
            "issuer_name": n.get("issuer_name") or b.get("issuer_name"),
            "nse_symbol": n.get("nse_symbol"),
            "nse_series": n.get("nse_series"),
            "bse_scrip_code": b.get("bse_scrip_code"),
            "bse_scrip_id": b.get("bse_scrip_id"),
        })

    active = sum(1 for r in bse_rows if r.get("bse_status") == "Active")
    suspended = sum(1 for r in bse_rows if r.get("bse_status") == "Suspended")
    stats = {
        "nse_corp": len(nse_by),
        "bse_active": active,
        "bse_suspended": suspended,
        "overlap": len(set(nse_by) & set(bse_by)),
        "notes": "active_only" if active_only else "active+suspended",
    }
    raws = {
        "nse_debt_csv": raw_csv,
        "bse_scrip_master": json.dumps(raw_master),
    }
    return records, stats, raws


def _record_failure(exc, log_lines, run_id=None):
    """Best-effort ok=false row in bond_runs so failed runs are queryable
    from the DB. Never masks the original error (e.g. if the DB itself is
    down, the cron log still gets the real traceback)."""
    try:
        from . import db
        db.log_failed_run(f"{type(exc).__name__}: {exc}",
                          log="\n".join(log_lines) or None, run_id=run_id)
    except Exception:  # noqa: BLE001
        pass


def run(active_only: bool = False, dry_run: bool = False) -> dict:
    started = time.monotonic()
    log_lines = []

    def log(msg):
        log_lines.append(f"{datetime.now(timezone.utc).isoformat()} {msg}")

    run_id = None
    try:
        records, stats, raws = build_records(active_only=active_only)
        log(f"fetched masters in {time.monotonic() - started:.1f}s: "
            f"NSE corp={stats['nse_corp']} BSE active={stats['bse_active']} "
            f"suspended={stats['bse_suspended']} overlap={stats['overlap']} "
            f"union={len(records)} ({stats['notes']})")

        result = {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "sources": stats,
            "union_total": len(records),
            "nse_only": sum(1 for r in records
                            if r["on_nse"] and not r["on_bse"]),
            "bse_only": sum(1 for r in records
                            if r["on_bse"] and not r["on_nse"]),
        }
        if dry_run:
            result["dry_run"] = True
            return result

        from . import db  # imported lazily so --dry-run needs no DB

        # Open the run row up front so raw files and the final summary all
        # reference the same run id.
        run_id = db.start_run()

        # Archive the raw masters first: even if reconciliation aborts, the
        # day's files are preserved for later re-processing.
        for source, content in raws.items():
            info = db.save_raw_file(source, content, run_id=run_id)
            log(f"raw {source}: {info['size']:,} bytes "
                f"sha256={info['sha256'][:12]} "
                f"{'archived' if info['stored'] else 'unchanged, skipped'}")

        result["changes"] = db.save_universe(records, stats,
                                             log="\n".join(log_lines),
                                             run_id=run_id)
        result["universe"] = db.universe_counts()
        return result
    except Exception as exc:  # noqa: BLE001
        if not dry_run:
            _record_failure(exc, log_lines, run_id)
        raise


def _print_human(res: dict):
    s = res["sources"]
    print(f"[{res['run_at']}] universe build "
          f"({'DRY-RUN' if res.get('dry_run') else 'saved'})", flush=True)
    print(f"  sources: NSE corp={s['nse_corp']}  "
          f"BSE active={s['bse_active']} suspended={s['bse_suspended']}  "
          f"overlap={s['overlap']}", flush=True)
    print(f"  union:   {res['union_total']}  "
          f"(NSE-only={res['nse_only']}, BSE-only={res['bse_only']})",
          flush=True)
    if "changes" in res:
        c = res["changes"]
        if c.get("aborted"):
            print(f"  ABORTED: {c['reason']}", flush=True)
            return
        print(f"  changes: +{c['added']} added, -{c['dropped']} dropped, "
              f"{c['relisted']} relisted, "
              f"{c['status_change']} status-change, "
              f"{c['identifier_change']} identifier-change", flush=True)
    if "universe" in res:
        u = res["universe"]
        print(f"  stored:  listed={u.get('listed')} "
              f"(NSE={u.get('on_nse')}, BSE={u.get('on_bse')}, "
              f"both={u.get('both')}), dropped={u.get('dropped')}", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the daily bond universe.")
    ap.add_argument("--active-only", action="store_true",
                    help="Exclude BSE Suspended (only actively-trading).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch and report without writing to the database.")
    ap.add_argument("--json", action="store_true",
                    help="Emit the run summary as JSON.")
    args = ap.parse_args(argv)

    try:
        res = run(active_only=args.active_only, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        msg = f"universe build FAILED: {type(exc).__name__}: {exc}"
        if args.json:
            print(json.dumps({"ok": False, "error": msg}))
        else:
            print(msg, file=sys.stderr, flush=True)
        return 1

    aborted = bool(res.get("changes", {}).get("aborted"))
    if args.json:
        print(json.dumps({"ok": not aborted, **res}, default=str))
    else:
        _print_human(res)

    if not args.dry_run:
        from . import db
        db.close_pool()
    return 1 if aborted else 0


if __name__ == "__main__":
    sys.exit(main())
