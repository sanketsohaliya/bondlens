import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture verified current-bond exchange records without altering NSDL data.")
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture", help="Fetch current records or explicit historical ranges")
    capture.add_argument("--source", choices=("all", "nse", "bse"), default="all")
    capture.add_argument("--mode", choices=("recent", "backfill", "refetch"), default="recent")
    capture.add_argument("--from", dest="date_from", type=date.fromisoformat,
                         help="Inclusive publication date (NSE) or ex-date (BSE); no implicit archive start")
    capture.add_argument("--to", dest="date_to", type=date.fromisoformat)
    capture.add_argument("--isin", action="append", help="Restrict a historical/refetch target set; repeat for several ISINs")
    capture.add_argument("--window-days", type=int, default=14)
    capture.add_argument("--request-budget", type=int, default=100, help="Maximum recent/refetch HTTP attempts per source")
    capture.add_argument("--history-budget", type=int, default=10, help="Maximum queued history HTTP attempts per source per run")
    capture.add_argument("--dry-run", action="store_true", help="Read scope/progress and print the plan without HTTP or database writes")
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
        if args.mode == "recent" and (args.date_from or args.date_to or args.isin):
            parser.error("recent capture uses the current universe and native rolling windows; use backfill/refetch for an explicit scope")
        if args.mode != "recent" and (args.date_from is None or args.date_to is None or args.date_from > args.date_to):
            parser.error("backfill/refetch requires --from and --to in ascending order")
    return args


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
        else:
            result = collector.capture(
                sources, mode=args.mode, date_from=args.date_from, date_to=args.date_to,
                only=set(args.isin) if args.isin else None, window_days=args.window_days,
                request_budget=args.request_budget, history_budget=args.history_budget, dry_run=args.dry_run,
            )
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
