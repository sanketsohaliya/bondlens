"""Audit lossy parser conversions against the full nsdl_raw layer.

For every field the parsers convert with to_num / to_int / to_date (or the
coupon-rate special case), scan the latest payload per (isin, endpoint) and
report every DISTINCT raw value that survives clean() but converts to None —
i.e. real data the parsed layer silently drops.

Usage (inside the jobs container):
    python scripts/audit_parse_values.py            # summary + failures
    python scripts/audit_parse_values.py --all      # also list top parsed values
"""
import argparse
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db
from backend.nsdl_parse import clean, to_date, to_int, to_num
from backend.nsdl_parse import coupon_rate_num as rate

# Field paths mirroring backend.nsdl_parse. '[]' iterates a list; a leading
# '[]' means the payload root is itself a list.
SPECS = {
    "instruments": [
        ("instrumentsVo.instruments.faceValue", to_num),
        ("instrumentsVo.instruments.issuePrice", to_num),
        ("instrumentsVo.instruments.totalIssueSize", to_num),
        ("instrumentsVo.instruments.amountRaised", to_num),
        ("instrumentsVo.instruments.allotmentDate", to_date),
        ("instrumentsVo.instruments.redemptionDate", to_date),
        ("instrumentsVo.instruments.tenureYears", to_int),
        ("instrumentsVo.instruments.tenureMonths", to_int),
        ("instrumentsVo.instruments.tenureDays", to_int),
        ("instrumentsVo.assetCover.assetCvrPercent", to_num),
        ("instrumentsVo.furtherIssue.furtherIssueVo[].allotmentDate", to_date),
        ("instrumentsVo.furtherIssue.furtherIssueVo[].allotmentQuantity", to_num),
        ("instrumentsVo.furtherIssue.furtherIssueVo[].issuePriceRs", to_num),
        ("instrumentsVo.furtherIssue.furtherIssueVo[].issueSizeRsCrs", to_num),
        ("instrumentsVo.furtherIssue.furtherIssueVo[].cumulativeQuantity", to_num),
        ("instrumentsVo.furtherIssue.furtherIssueVo[].cummIssueSizeRsCrs", to_num),
    ],
    "coupondetail": [
        ("coupensVo.couponDetails.couponRate", rate),
        ("coupensVo.cashFlowScheduleDetails.cashFlowSchedule[].dueDate", to_date),
        ("coupensVo.cashFlowScheduleDetails.cashFlowSchedule[].recordDate", to_date),
        ("coupensVo.cashFlowScheduleDetails.cashFlowSchedule[].paymentDate", to_date),
        ("coupensVo.cashFlowScheduleDetails.cashFlowSchedule[].amountPayable", to_num),
        ("coupensVo.cashFlowScheduleDetails.cashFlowSchedule[].amountRedeem", to_num),
        ("coupensVo.cashFlowScheduleDetails.cashFlowSchedule[].amntOutstanding", to_num),
        ("coupensVo.cashFlowScheduleDetails.cashFlowSchedule[].actualRecordDate", to_date),
        ("coupensVo.cashFlowScheduleDetails.cashFlowSchedule[].actualPaymentDate", to_date),
    ],
    "redemptions": [
        ("netQuantity", to_num),
        ("redemption[].partialRedemptionDates", to_date),
        ("redemption[].valueRedeemed", to_num),
        ("redemption[].quantityRedeemed", to_num),
    ],
    "credit_ratings": [
        ("currentRatings[].creditRatingDate", to_date),
        ("currentRatings[].ratingChangeDate", to_date),
        ("earlierRatings[].creditRatingDate", to_date),
        ("earlierRatings[].ratingChangeDate", to_date),
    ],
    "listings": [
        ("listingDetails[].listingDate", to_date),
        ("listingDetails[].allotmentDate", to_date),
        ("listingDetails[].listingQuantity", to_num),
        ("earlierListingDetails[].listingDate", to_date),
        ("earlierListingDetails[].allotmentDate", to_date),
        ("earlierListingDetails[].listingQuantity", to_num),
    ],
    "defaultdetail": [
        ("[].srNo", to_int),
        ("[].issueSize", to_num),
        ("[].interestDueDate", to_date),
        ("[].redemptionDueDate", to_date),
        ("[].verificationDate", to_date),
    ],
    "technical_default": [
        ("[].paymtDueDate", to_date),
        ("[].amtToBePaid", to_num),
        ("[].amtPaid", to_num),
        ("[].amtPaymtFailed", to_num),
        ("[].updateDt", to_date),
    ],
    "restructuring": [
        ("allotmentDate", to_date),
        ("redemptionDate", to_date),
        ("parentAllotmentDate", to_date),
        ("parentRedemptionDate", to_date),
    ],
    "publicshareholderdetails": [
        ("reportGenerationDate", to_date),
        ("shareholdingData[].headerEmployeeCount", to_int),
        ("shareholdingData[].totalShareCountHeader", to_num),
        ("shareholdingData[].listOfSubHeader[].subHeaderEmployeeCount", to_int),
        ("shareholdingData[].listOfSubHeader[].totalShareCountSubHeader", to_num),
    ],
}

LATEST = ("SELECT DISTINCT ON (isin, quarter) isin, payload "
          "FROM nsdl_raw WHERE endpoint = %s AND http_status = 200 "
          "ORDER BY isin, quarter, fetched_at DESC")


def extract(node, segments):
    """Yield raw values at a '[]'-aware dotted path."""
    if not segments:
        yield node
        return
    seg, rest = segments[0], segments[1:]
    if seg == "[]":
        for item in node if isinstance(node, list) else []:
            yield from extract(item, rest)
    elif isinstance(node, dict):
        nxt = node.get(seg)
        if seg.endswith("[]"):  # 'key[]' -> key holding a list
            for item in node.get(seg[:-2]) or []:
                yield from extract(item, rest)
        elif nxt is not None:
            yield from extract(nxt, rest)


def split_path(path):
    out = []
    for seg in path.split("."):
        if seg.endswith("[]") and seg != "[]":
            out.extend([seg[:-2], "[]"])
        else:
            out.append(seg)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="Also print the top parsed values per field.")
    ap.add_argument("--max-values", type=int, default=15,
                    help="Distinct failing values shown per field.")
    args = ap.parse_args()

    t0 = time.time()
    totals = Counter()           # (endpoint, path) -> non-null raw values
    failures = defaultdict(Counter)   # (endpoint, path) -> raw value -> count
    fail_isin = {}               # (endpoint, path, raw value) -> sample isin
    parsed_vals = defaultdict(Counter)

    with db._conn() as conn:
        for endpoint, specs in SPECS.items():
            paths = [(p, split_path(p), conv) for p, conv in specs]
            with conn.cursor(name=f"audit_{endpoint}") as cur:
                cur.execute(LATEST, (endpoint,))
                for row in cur:
                    isin, payload = row["isin"], row["payload"]
                    for path, segs, conv in paths:
                        for raw in extract(payload, segs):
                            if clean(raw) is None:
                                continue  # legit null marker
                            key = (endpoint, path)
                            totals[key] += 1
                            val = conv(raw)
                            if val is None:
                                failures[key][str(raw)[:120]] += 1
                                fail_isin.setdefault(
                                    (endpoint, path, str(raw)[:120]), isin)
                            elif args.all:
                                parsed_vals[key][val] += 1

    bad_fields = 0
    for key in sorted(totals):
        endpoint, path = key
        n, fails = totals[key], failures.get(key)
        status = f"FAIL {sum(fails.values())}" if fails else "ok"
        print(f"{endpoint:24s} {path:70s} {n:>9,}  {status}", flush=True)
        if fails:
            bad_fields += 1
            for raw, cnt in fails.most_common(args.max_values):
                sample = fail_isin[(endpoint, path, raw)]
                print(f"    {cnt:>7,}x  {sample}  {raw!r}", flush=True)
            extra = len(fails) - args.max_values
            if extra > 0:
                print(f"    ... {extra} more distinct values", flush=True)
        if args.all and parsed_vals.get(key):
            for val, cnt in parsed_vals[key].most_common(5):
                print(f"    parsed {cnt:>7,}x  {val}", flush=True)

    print(f"\n{bad_fields} field(s) with dropped values; "
          f"done in {time.time() - t0:.0f}s", flush=True)
    db.close_pool()
    return 1 if bad_fields else 0


if __name__ == "__main__":
    sys.exit(main())
