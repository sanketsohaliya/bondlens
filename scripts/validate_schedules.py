"""Run the schedule engine across every bond and report parse coverage.

The quality gate for the analytics core: classifies each ISIN's schedule
shape, reconciles principal against face value, and prints the coverage
summary plus samples of whatever resists classification — so parser
improvements are driven by actual data, not assumptions.

    python scripts/validate_schedules.py            # summary
    python scripts/validate_schedules.py -v         # + warning samples
"""
import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bondlens import db  # noqa: E402
from bondlens.analytics import schedule  # noqa: E402


def load_all():
    with db._conn() as conn:
        terms = {r["isin"]: r for r in conn.execute(
            "SELECT * FROM bond_terms").fetchall()}
        flows = defaultdict(list)
        for r in conn.execute(
                "SELECT isin, event, due_date, record_date, amount_payable, "
                "amount_redeem, amount_outstanding FROM bond_cashflows "
                "ORDER BY isin, due_date").fetchall():
            flows[r["isin"]].append(r)
        traded = {r["isin"] for r in conn.execute(
            "SELECT DISTINCT isin FROM eod_trades").fetchall()}
        listed = set(db.universe_isins())
    return terms, flows, traded, listed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    terms, flows, traded, listed = load_all()
    isins = sorted(listed | set(flows))

    shapes = Counter()
    usable = Counter()      # usable-for-exact-YTM by shape
    traded_usable = 0
    traded_total = 0
    estimated = 0
    warning_kinds = Counter()
    samples = defaultdict(list)

    for isin in isins:
        s = schedule.build(isin, flows.get(isin, []), terms.get(isin))
        shapes[s.shape] += 1
        if s.usable:
            usable[s.shape] += 1
        if s.amounts_estimated:
            estimated += 1
        for w in s.warnings:
            kind = w.split(" ")[0] if not w.startswith("redemption") else "unreconciled"
            warning_kinds[kind] += 1
            if len(samples[kind]) < 5:
                samples[kind].append((isin, w))
        if isin in traded:
            traded_total += 1
            if s.usable:
                traded_usable += 1

    total = len(isins)
    total_usable = sum(usable.values())
    print(f"bonds evaluated: {total}")
    print(f"{'shape':<14}{'count':>8}{'usable':>8}")
    for shape, n in shapes.most_common():
        print(f"{shape:<14}{n:>8}{usable.get(shape, 0):>8}")
    print(f"\nusable for exact YTM: {total_usable}/{total} "
          f"({total_usable / total:.1%})")
    if traded_total:
        print(f"traded ISINs usable:  {traded_usable}/{traded_total} "
              f"({traded_usable / traded_total:.1%})  <- the target metric")
    print(f"schedules with estimated amounts: {estimated}")

    if warning_kinds:
        print("\nwarnings:")
        for kind, n in warning_kinds.most_common():
            print(f"  {kind:<16}{n:>7}")
            if args.verbose:
                for isin, w in samples[kind]:
                    print(f"      {isin}: {w[:90]}")

    db.close_pool()


if __name__ == "__main__":
    main()
