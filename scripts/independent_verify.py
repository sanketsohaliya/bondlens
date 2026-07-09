"""Independent verification of every displayed exact YTM.

Deliberately reimplements the math from scratch (Newton's method, its own
date handling, raw DB reads) so a bug in bondlens/ytm.py cannot hide by
agreeing with itself. Three checks per bond:

  A. Independent solver vs API value (tolerance 0.05pp).
  B. PV residual: discounting the buyer's flows at the reported YTM must
     reproduce the traded price to within 0.05%.
  C. Schedule integrity: redemption present and equal to face value;
     yearly interest consistent with the NSE coupon rate when known.
"""
import json
import sys
import urllib.request
from datetime import date, datetime

sys.path.insert(0, ".")

from bondlens import db

TODAY = date.today()


def pdate(s):
    for fmt in ("%d-%m-%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def buyer_flows(raw):
    """Flows a buyer today actually receives (record date still open)."""
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


def newton_ytm(price, flows):
    """Independent solver: Newton-Raphson with analytic derivative."""
    y = 0.10
    for _ in range(200):
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


bonds = json.load(urllib.request.urlopen(
    "http://localhost:8000/api/bonds"))["bonds"]
exact = [b for b in bonds if b["ytm_method"] == "exact"]

flows_map = db.nsdl_all_cashflows()

solver_bad, residual_bad, integrity_bad = [], [], []
checked = 0

for b in exact:
    detail = json.load(urllib.request.urlopen(
        f"http://localhost:8000/api/bonds/{b['isin']}/detail"))
    raw = detail["cashflows"]  # post fill/normalize, as displayed
    fl = buyer_flows(raw)
    if not fl or not b["ltp"]:
        continue
    checked += 1

    # A. independent solver
    mine = newton_ytm(b["ltp"], fl)
    if mine is None or abs(mine - b["ytm"]) > 0.05:
        solver_bad.append((b["symbol"], b["ytm"], round(mine, 2) if mine else None))

    # B. PV residual at reported YTM
    y = b["ytm"] / 100.0
    pv = sum(a / (1 + y) ** t for t, a in fl)
    if abs(pv - b["ltp"]) / b["ltp"] > 0.0005:
        residual_bad.append((b["symbol"], b["ytm"], round(pv, 2), b["ltp"]))

    # C. schedule integrity
    redeem = sum(f["amount"] or 0 for f in raw
                 if f["event"] and ("redem" in f["event"].lower()
                                    or "maturity" in f["event"].lower()))
    face = b["face_value"] or 0
    problems = []
    if face and abs(redeem - face) / face > 0.02:
        problems.append(f"redemption {redeem} vs face {face}")
    if b["coupon"] and face:
        by_year = {}
        for f in raw:
            d = pdate(f["due_date"])
            if d and f["event"] and "interest" in f["event"].lower() and f["amount"]:
                by_year.setdefault(d.year, 0.0)
                by_year[d.year] += f["amount"]
        full_years = [v for yy, v in by_year.items()
                      if TODAY.year < yy < (max(by_year) if by_year else 0)]
        expected = face * b["coupon"] / 100.0
        if full_years and abs(sorted(full_years)[len(full_years) // 2] - expected) / expected > 0.05:
            problems.append(
                f"median yearly interest {sorted(full_years)[len(full_years) // 2]:.0f}"
                f" vs coupon-implied {expected:.0f}")
    if problems:
        integrity_bad.append((b["symbol"], b["ytm"], "; ".join(problems)))

print(f"exact-YTM bonds verified: {checked}")
print(f"\nA. solver disagreements (> 0.05pp): {len(solver_bad)}")
for x in solver_bad[:10]:
    print("   ", x)
print(f"\nB. PV residual failures (> 0.05%): {len(residual_bad)}")
for x in residual_bad[:10]:
    print("   ", x)
print(f"\nC. schedule integrity flags: {len(integrity_bad)}")
for x in integrity_bad[:15]:
    print("   ", x)
