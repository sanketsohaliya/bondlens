"""Fetch the Indian G-Sec yield curve from NSE's live bonds endpoint.

Same cookie handshake as the corporate bond fetch, with ``type=gsec``.
Central government bonds (GS series) encode coupon and maturity year in the
symbol, e.g. ``754GS2036`` = 7.54% maturing 2036. Exact maturity dates are
not in the payload, so mid-year is assumed — a few basis points of error,
mostly at the short end.
"""
import re
from typing import Dict, List, Optional

import requests

from .nse import BASE_URL, HEADERS
from .ytm import parse_date, years_to_maturity, ytm

GSEC_URL = f"{BASE_URL}/api/liveBonds-traded-on-cm?type=gsec"

_SYMBOL_RE = re.compile(r"^(\d{2,3})GS(\d{4})$")


def fetch_curve(timeout: int = 15) -> List[Dict]:
    """Curve points [{tenor, yield, symbol, traded_value}] sorted by tenor.

    One point per maturity year — the most-traded bond wins, so quiet odd
    prints don't distort the curve.
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    session.get(BASE_URL, timeout=timeout)
    resp = session.get(GSEC_URL, timeout=timeout)
    resp.raise_for_status()

    best: Dict[str, Dict] = {}
    for row in resp.json().get("data", []):
        if row.get("series") != "GS":
            continue
        m = _SYMBOL_RE.match(row.get("symbol") or "")
        price = row.get("lastPrice")
        if not m or not price:
            continue
        digits, year = m.groups()
        coupon = float(digits) / (10 if len(digits) == 2 else 100)
        maturity = f"01-Jul-{year}"
        y = ytm(float(price), coupon, 100.0, maturity)
        tenor = years_to_maturity(parse_date(maturity))
        traded = row.get("totalTradedValue") or 0
        # Discard implausible yields from stale/odd prints.
        if y is None or tenor is None or not 3.0 <= y <= 13.0:
            continue
        cur = best.get(year)
        if cur is None or traded > cur["traded_value"]:
            best[year] = {
                "tenor": round(tenor, 2),
                "yield": y,
                "symbol": row["symbol"],
                "traded_value": traded,
            }
    return _clean_curve(sorted(best.values(), key=lambda p: p["tenor"]))


_MAX_CHORD_DEV = 0.40  # pp deviation from flanking chord before a point is dropped


def _clean_curve(points: List[Dict]) -> List[Dict]:
    """Drop stale prints that would make the curve zigzag.

    Untraded bonds carry old prices, so first prefer points with actual
    volume. Then iteratively remove whichever interior point deviates most
    from the straight line between its flanking neighbors — slope-aware, so
    a legitimately steep short end survives while a stale print sticking out
    of an otherwise smooth stretch is culled. Edge points are never dropped
    (no second flank to judge them against).
    """
    traded = [p for p in points if p["traded_value"] > 0]
    if len(traded) >= 6:
        points = traded

    points = list(points)
    while len(points) > 4:
        worst_i, worst_dev = None, _MAX_CHORD_DEV
        for i in range(1, len(points) - 1):
            lo, hi = points[i - 1], points[i + 1]
            if hi["tenor"] == lo["tenor"]:
                continue
            frac = (points[i]["tenor"] - lo["tenor"]) / (hi["tenor"] - lo["tenor"])
            chord = lo["yield"] + (hi["yield"] - lo["yield"]) * frac
            dev = abs(points[i]["yield"] - chord)
            if dev > worst_dev:
                worst_i, worst_dev = i, dev
        if worst_i is None:
            break
        points.pop(worst_i)
    return points


def interpolate(points: List[Dict], years: Optional[float]) -> Optional[float]:
    """Linear interpolation of the curve at ``years``; clamps at the ends."""
    if years is None or not points:
        return None
    pts = [(p["tenor"], p["yield"]) for p in points]
    if years <= pts[0][0]:
        return pts[0][1]
    if years >= pts[-1][0]:
        return pts[-1][1]
    for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
        if t0 <= years <= t1:
            if t1 == t0:
                return y0
            return round(y0 + (y1 - y0) * (years - t0) / (t1 - t0), 2)
    return None
