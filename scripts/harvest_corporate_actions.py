import argparse
import json
import math
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture verified current-bond exchange records without altering NSDL data.")
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture", help="Fetch current records or an explicit bounded historical range")
    capture.add_argument("--source", choices=("all", "nse", "bse"), default="all")
    capture.add_argument("--mode", choices=("recent", "backfill", "refetch"), default="recent")
    capture.add_argument("--from", dest="date_from", type=date.fromisoformat,
                         help="Inclusive publication date (NSE) or ex-date (BSE); no implicit archive start")
    capture.add_argument("--to", dest="date_to", type=date.fromisoformat)
    capture.add_argument("--isin", action="append", help="Restrict a historical/refetch target set; repeat for several ISINs")
    capture.add_argument("--window-days", type=int, default=14)
    capture.add_argument("--request-budget", type=int, default=100, help="Maximum HTTP attempts per source for this capture")
    capture.add_argument("--history-budget", type=int, default=0, help="Optional inline history attempts; default 0, use the separate history worker instead")
    capture.add_argument("--dispatch-history", action="store_true", help="Start a detached history worker only when enrolled goals have missing work")
    capture.add_argument("--lock-wait-seconds", type=float, default=120, help="Bounded wait for an active history batch to release a source")
    capture.add_argument("--dry-run", action="store_true", help="Read scope/progress and print the plan without HTTP, process launches or database writes")
    history = sub.add_parser("history", help="Resume enrolled historical goals until finished, then exit")
    history.add_argument("--source", choices=("all", "nse", "bse"), default="all")
    history.add_argument("--batch-requests", type=int, default=10, help="Request attempts per source before yielding to recent capture")
    history.add_argument("--window-days", type=int, default=14)
    history.add_argument("--pause-seconds", type=float, default=2, help="Polite pause between batches; history continues in this process")
    replay = sub.add_parser("replay", help="Reindex pending retained evidence without exchange requests")
    replay.add_argument("--source", choices=("all", "nse", "bse"), default="all")
    replay.add_argument("--run-id", type=int)
    replay.add_argument("--reindex", action="store_true", help="Explicitly rebuild metadata for already-indexed retained evidence too")
    status = sub.add_parser("status", help="Read-only capture freshness, progress and exclusion summary")
    status.add_argument("--source", choices=("all", "nse", "bse"), default="all")
    args = parser.parse_args(argv)
    if args.command == "capture":
        if args.window_days < 1 or args.request_budget < 1 or args.history_budget < 0:
            parser.error("window-days/request-budget must be positive and history-budget nonnegative")
        if not math.isfinite(args.lock_wait_seconds) or args.lock_wait_seconds < 0:
            parser.error("lock-wait-seconds must be finite and nonnegative")
        if args.mode == "recent" and (args.date_from or args.date_to or args.isin):
            parser.error("recent capture uses the current universe and native rolling windows; use backfill/refetch for an explicit scope")
        if args.mode != "recent" and (args.date_from is None or args.date_to is None or args.date_from > args.date_to):
            parser.error("backfill/refetch requires --from and --to in ascending order")
        if args.dispatch_history and (args.mode != "recent" or args.history_budget):
            parser.error("dispatch-history requires recent mode with no inline history budget")
    if args.command == "history":
        if args.batch_requests < 1 or args.window_days < 1 or not math.isfinite(args.pause_seconds) or args.pause_seconds < 0:
            parser.error("history budgets must be positive; pause-seconds must be finite and nonnegative")
    return args


def dispatch_history(collector: Any, sources: list[str], launch: Callable[..., Any] | None = None) -> dict:
    from backend.corporate_action_sources import SOURCES

    if not sources:
        return {"state": "idle", "sources": []}
    statuses = collector.history_status(sources)
    pending = [s for s in dict.fromkeys(sources)
               if statuses[s].get("has_work", statuses[s].get("state") == "pending")]
    if not pending:
        return {"state": "idle", "sources": []}
    available = []
    for source in pending:
        with collector.store.lock("history_worker:" + source) as acquired:
            if acquired:
                available.append(source)
    if not available:
        return {"state": "already_running", "sources": pending}
    selection = "all" if len(available) == len(SOURCES) else "nse" if available[0] == SOURCES[0] else "bse"
    process = (launch or subprocess.Popen)(
        [sys.executable, str(Path(__file__).resolve()), "history", "--source", selection],
        cwd=str(Path(__file__).resolve().parent.parent),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True,
    )
    return {"state": "started", "sources": available, "pid": process.pid}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from backend import db
    from backend.corporate_action_sources import SOURCES, ExchangeClient
    from backend.corporate_action_store import Store
    from backend.corporate_actions import Collector

    sources: list[str] = list(SOURCES) if args.source == "all" else [SOURCES[0 if args.source == "nse" else 1]]
    store = Store()
    client = ExchangeClient()
    try:
        collector = Collector(store, client)
        if args.command == "status":
            snapshot = store.snapshot()
            result = {s: snapshot.get(s, {}) for s in sources}
        elif args.command == "replay":
            result = collector.replay(sources, args.run_id, reindex=args.reindex)
        elif args.command == "history":
            result = collector.drain_history(
                sources, batch_requests=args.batch_requests, window_days=args.window_days,
                pause_seconds=args.pause_seconds,
                on_progress=lambda event: print(json.dumps(event, default=str), flush=True),
            )
        else:
            result = collector.capture(
                sources, mode=args.mode, date_from=args.date_from, date_to=args.date_to,
                only=set(args.isin) if args.isin else None, window_days=args.window_days,
                request_budget=args.request_budget, history_budget=args.history_budget, dry_run=args.dry_run,
                lock_wait_seconds=args.lock_wait_seconds,
            )
            if args.dispatch_history and not args.dry_run:
                healthy = [s for s in sources if result["sources"].get(s, {}).get("recent", {}).get("ok")]
                result["history_dispatch"] = dispatch_history(collector, healthy)
        print(json.dumps(result, default=str, indent=2))
        return 0 if result.get("ok", True) else 1
    except Exception as exc:
        message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        print(json.dumps({"ok": False, "error": message}), file=sys.stderr)
        return 1
    finally:
        client.close()
        db.close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
