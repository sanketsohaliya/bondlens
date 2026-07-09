"""Census + verification of EVERY bond in the latest snapshot.

Buckets every bond, then verifies each bucket:
  - exact:   independent Newton solver + PV residual + integrity flags
  - approx:  independent reimplementation of the annual model, compared
  - no YTM:  reason census (no price, matured, missing coupon, refused)
"""
import json
import sys
import urllib.request
from datetime import date, datetime

sys.path.insert(0, ".")

from bondlens import db, nsdl
from bondlens.app import coupon_from_symbol

TODAY = date.today()


def pdate(s):
    for fmt in ("%d-%m-%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def buyer_flows(raw):
    out = []
    for f in raw:
        due = pdate(f["due_date"])
        rec = pdate(f.get("record_date"))
        if due is None or due <= TODAY:
            continue
        if rec is not None and rec <= TODAY:
            continue
        if not f["amount"]:
            continue
        out.append(((due - TODAY).days / 365.0, float(f["amount"])))
    return out


def newton(price, flows, guess=0.10):
    y = guess
    for _ in range(300):
        pv = sum(a / (1 + y) ** t for t, a in flows)
        dpv = sum(-t * a / (1 + y) ** (t + 1) for t, a in flows)
        if abs(dpv) < 1e-12:
            return None
        step = (pv - price) / dpv
        y -= step
        if y <= -0.99:
            y = -0.98
        if abs(step) < 1e-12:
            return y * 100
    return None


def independent_approx(price, coupon_pct, face, maturity, window=15 / 365.0):
    """Own implementation of the annual-coupon estimate with record window."""
    if not price or not face or maturity is None or maturity <= TODAY:
        return None
    years = (maturity - TODAY).days / 365.0
    flows = [(years, face)]
    t = years
    coupon = face * (coupon_pct or 0.0) / 100.0
    while t > window:
        if coupon:
            flows.append((t, coupon))
        t -= 1.0
    return newton(price, flows)


bonds = json.load(urllib.request.urlopen(
    "http://localhost:8000/api/bonds"))["bonds"]
flows_map = db.nsdl_all_cashflows()
meta_map = db.nsdl_meta_map()

buckets = {"exact": [], "approx": [], "none": []}
for b in bonds:
    buckets[b["ytm_method"] or "none"].append(b)

print(f"total bonds: {len(bonds)}")
print(f"  exact: {len(buckets['exact'])}  approx: {len(buckets['approx'])}"
      f"  none: {len(buckets['none'])}")

# --- exact bucket ---
solver_bad, residual_bad, integrity = [], [], []
for b in buckets["exact"]:
    meta = meta_map.get(b["isin"])
    freq = nsdl.classify_frequency(meta["frequency_text"]) if meta else None
    flows, _ = nsdl.fill_amounts(
        flows_map.get(b["isin"], []),
        b["coupon"] or coupon_from_symbol(b["symbol"]),
        b["face_value"], freq)
    flows = nsdl.normalize_flows(flows, b["face_value"])
    fl = buyer_flows(flows)
    if not fl:
        solver_bad.append((b["symbol"], b["ytm"], "no buyer flows?!"))
        continue
    mine = newton(b["ltp"], fl)
    if mine is None or abs(mine - b["ytm"]) > 0.05:
        solver_bad.append((b["symbol"], b["ytm"],
                           round(mine, 2) if mine is not None else None))
    y = b["ytm"] / 100.0
    pv = sum(a / (1 + y) ** t for t, a in fl)
    if abs(pv - b["ltp"]) / b["ltp"] > 0.001:
        residual_bad.append((b["symbol"], b["ytm"], round(pv, 2), b["ltp"]))
    redeem = sum(f["amount"] or 0 for f in flows
                 if f.get("event") and nsdl._REDEEM_RE.search(f["event"]))
    if b["face_value"] and abs(redeem - b["face_value"]) / b["face_value"] > 0.02:
        integrity.append((b["symbol"], b["ytm"],
                          f"redeem {redeem:.0f} vs face {b['face_value']:.0f}"))

print(f"\nEXACT ({len(buckets['exact'])}):")
print(f"  solver disagreements: {len(solver_bad)}", solver_bad[:8])
print(f"  PV residual failures: {len(residual_bad)}", residual_bad[:8])
print(f"  redemption/face notes: {len(integrity)} (ZC & amortizers expected)")

# --- approx bucket ---
approx_bad = []
for b in buckets["approx"]:
    mine = independent_approx(b["ltp"], b["coupon"],
                              b["face_value"], pdate(b["maturity_date"]))
    if mine is None:
        if b["ytm"] is not None:
            approx_bad.append((b["symbol"], b["ytm"], None))
    elif abs(mine - b["ytm"]) > 0.05:
        approx_bad.append((b["symbol"], b["ytm"], round(mine, 2)))
print(f"\nAPPROX ({len(buckets['approx'])}):")
print(f"  disagreements with independent reimplementation: {len(approx_bad)}")
for x in approx_bad[:10]:
    print("   ", x)
suspicious = [(b["symbol"], b["ytm"]) for b in buckets["approx"]
              if b["ytm"] is not None and (b["ytm"] > 25 or b["ytm"] < 0)]
print(f"  displayed approx outside 0-25% (eyeball these): {len(suspicious)}")
for x in sorted(suspicious, key=lambda x: -abs(x[1]))[:10]:
    print("   ", x)

# --- none bucket ---
reasons = {}
for b in buckets["none"]:
    m = pdate(b["maturity_date"])
    if not b["ltp"]:
        r = "no traded price"
    elif m is None:
        r = "no maturity date"
    elif m <= TODAY:
        r = "matured / matures today"
    elif not b["face_value"]:
        r = "no face value"
    elif not b["coupon"] and not flows_map.get(b["isin"]):
        r = "no coupon info & no schedule"
    else:
        r = "solver refused (unbracketable/implausible)"
    reasons.setdefault(r, []).append(b["symbol"])
print(f"\nNO YTM ({len(buckets['none'])}):")
for r, syms in sorted(reasons.items(), key=lambda kv: -len(kv[1])):
    print(f"  {r}: {len(syms)}", syms[:6])
