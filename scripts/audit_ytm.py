"""Cross-check approx vs exact YTM across the whole universe."""
import json
import urllib.request

d = json.load(urllib.request.urlopen(
    "http://localhost:8000/api/bonds"))["bonds"]

both = [b for b in d if b["ytm_method"] == "exact" and b["ytm"] is not None]
print("bonds with exact YTM:", len(both))

diffs = []
for b in both:
    r = json.load(urllib.request.urlopen(
        f"http://localhost:8000/api/bonds/{b['isin']}/detail"))
    if r["ytm_approx"] is not None and r["ytm_exact"] is not None:
        diffs.append((abs(r["ytm_approx"] - r["ytm_exact"]), b["symbol"],
                      r["ytm_exact"], r["ytm_approx"],
                      r.get("amounts_estimated")))

diffs.sort(reverse=True)
close = sum(1 for x in diffs if x[0] <= 1.0)
print(f"approx within 1pp of exact: {close}/{len(diffs)}")
print("largest deviations (|diff|, symbol, exact, approx, est_amounts):")
for x in diffs[:12]:
    print("  ", x)
