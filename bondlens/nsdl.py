"""Fetch exact bond cash-flow schedules from NSDL India Bond Info.

Public, unauthenticated JSON API discovered via the site's network traffic:
https://www.indiabondinfo.nsdl.com/bds-service/v1/public/bdsinfo/coupondetail?isin=...

Returns coupon basis, payment frequency text, and the complete dated
cash-flow schedule (interest + redemption) with exact rupee amounts per
unit of face value. Schedules are static, so they are cached permanently.
"""
import re
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import requests

from .ytm import parse_date

COUPON_URL = (
    "https://www.indiabondinfo.nsdl.com/bds-service/v1/public/bdsinfo/coupondetail"
)

# Sibling endpoint (found in the CBDS Angular bundle): current + earlier
# credit ratings per ISIN, entered and verified by the rating agencies
# themselves (datasource "CRA") — unlike NSE's rating field, which is
# frequently stale at-issuance data.
RATINGS_URL = (
    "https://www.indiabondinfo.nsdl.com/bds-service/v1/public/bdsinfo/credit-ratings"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _num(value) -> Optional[float]:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


_TIMES_PER_YEAR = {12: "monthly", 4: "quarterly", 2: "semi-annual", 1: "annual"}


def classify_frequency(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = text.upper()
    if "MONTHLY" in t:
        return "monthly"
    if "QUARTERLY" in t:
        return "quarterly"
    if "HALF" in t or "SEMI" in t:
        return "semi-annual"
    if "ANNUAL" in t or "YEARLY" in t:
        return "annual"
    if "MATURITY" in t or "CUMULATIVE" in t:
        return "cumulative"
    m = re.search(r"(\d+)\s*TIMES?", t)
    if m:
        return _TIMES_PER_YEAR.get(int(m.group(1)), "other")
    if "ONCE" in t or "TWICE" in t:
        return "annual" if "ONCE" in t else "semi-annual"
    return "other"


_REDEEM_RE = re.compile(r"redem|maturity", re.I)


def normalize_flows(flows: List[Dict], face_value: Optional[float]) -> List[Dict]:
    """Scale cash flows to per-unit amounts.

    NSDL's amountPayable is per bond for most issues but covers the whole
    issue for some (e.g. INE413U07418 reports 20,000 units' worth). The tell:
    total redemption should equal one unit's face value. If it is an integer
    multiple of face value, divide everything down by that count.

    The multiple must be >= 100: real issues are thousands of units, whereas
    partially amortized bonds can legitimately show small ratios (schedule
    covers the original face, NSE reports the reduced current face) and must
    never be scaled.
    """
    if not flows or not face_value:
        return flows
    total_redeem = sum(
        f["amount"] or 0.0 for f in flows
        if f.get("event") and _REDEEM_RE.search(f["event"])
    )
    if total_redeem <= 0:
        return flows
    scale = total_redeem / face_value
    n = round(scale)
    if n >= 100 and abs(scale - n) / n < 0.01:
        return [{**f, "amount": round((f["amount"] or 0.0) / n, 2)} for f in flows]
    return flows


def redemption_matches_face(
    flows: List[Dict], face_value: Optional[float], today: Optional[date] = None
) -> bool:
    """True when the schedule's future principal adds up to NSE's face value.

    Amortizing bonds (e.g. NHIT's staggered NCDs) repay principal in
    instalments; NSE quotes the price against the *residual* face, while
    NSDL's schedule sometimes still carries already-paid or duplicated
    principal rows. Feeding those into the XIRR yields spectacular but
    fictional numbers (79NHIT40 showed 19% against ~8% real), so exact
    YTM and duration must fall back to the approximation instead.
    """
    if not flows or not face_value:
        return True
    today = today or date.today()
    saw_redemption = False
    total = 0.0
    for f in flows:
        if not (f.get("event") and _REDEEM_RE.search(f["event"])):
            continue
        saw_redemption = True
        due = parse_date(f.get("due_date"))
        if due is None or due <= today:
            continue
        record = parse_date(f.get("record_date"))
        if record is not None and record <= today:
            continue
        total += f["amount"] or 0.0
    if not saw_redemption:
        return True  # nothing to compare — other guards handle this
    return abs(total - face_value) / face_value <= 0.02


_FREQ_PER_YEAR = {"monthly": 12, "quarterly": 4, "semi-annual": 2, "annual": 1}


def _periods_per_year(flows: List[Dict], frequency: Optional[str]) -> Optional[int]:
    """Infer coupon frequency from the spacing of interest due dates,
    falling back to the classified frequency text."""
    dates = sorted(
        d for f in flows
        if f.get("event") and not _REDEEM_RE.search(f["event"])
        and (d := parse_date(f["due_date"])) is not None
    )
    gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 10]
    if gaps:
        gaps.sort()
        median = gaps[len(gaps) // 2]
        return min((12, 6, 4, 3, 2, 1), key=lambda n: abs(365.25 / n - median))
    return _FREQ_PER_YEAR.get(frequency or "")


def fill_amounts(
    flows: List[Dict],
    coupon_pct: Optional[float],
    face_value: Optional[float],
    frequency: Optional[str] = None,
) -> Tuple[List[Dict], bool]:
    """Fill missing rupee amounts on NSDL's real dates.

    Full redemptions get face value; interest rows get coupon divided by the
    payment frequency. Returns (flows, estimated). If any row still cannot be
    filled (unknown coupon, partial redemption without amount), fall back to
    only the rows that carried real amounts.
    """
    if all(f["amount"] is not None for f in flows):
        return flows, False
    fallback = [f for f in flows if f["amount"] is not None]
    if not face_value:
        return fallback, False
    ppy = _periods_per_year(flows, frequency)
    filled = []
    for f in flows:
        if f["amount"] is not None:
            filled.append(f)
        elif f.get("event") and _REDEEM_RE.search(f["event"]):
            if "full" not in f["event"].lower():
                return fallback, False
            filled.append({**f, "amount": float(face_value)})
        elif coupon_pct and ppy:
            filled.append(
                {**f, "amount": round(face_value * coupon_pct / 100.0 / ppy, 2)}
            )
        else:
            return fallback, False
    return filled, True


def fetch_schedule(isin: str, timeout: int = 30) -> Tuple[Dict, List[Dict]]:
    """Fetch and parse the coupon detail for one ISIN.

    Returns (meta, cashflows). Raises on HTTP errors so callers can decide
    on retry policy.
    """
    resp = requests.get(COUPON_URL, params={"isin": isin}, headers=HEADERS,
                        timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    vo = data.get("coupensVo") or {}
    details = vo.get("couponDetails") or {}
    freq_text = details.get("interestPaymentFrequency")
    meta = {
        "frequency_text": freq_text,
        "frequency": classify_frequency(freq_text),
        "coupon_basis": details.get("couponBasis"),
    }

    flows = []
    schedule = (vo.get("cashFlowScheduleDetails") or {}).get("cashFlowSchedule") or []
    for row in schedule:
        if not row.get("dueDate"):
            continue
        amount = _num(row.get("amountPayable"))
        redeem = _num(row.get("amountRedeem"))
        total = (amount or 0.0) + (redeem or 0.0)
        # Some issues have real dates but blank amounts; keep the row —
        # fill_amounts() can compute the rupees from coupon and face value.
        flows.append(
            {
                "due_date": row.get("dueDate"),
                "record_date": row.get("recordDate"),
                "event": row.get("cashFlowsEvent"),
                "amount": total if total > 0 else None,
            }
        )
    return meta, flows


# Long registered names -> the short codes NSE uses, so both sources line up.
_AGENCY_SHORT = [
    ("CRISIL", "CRISIL"), ("ICRA", "ICRA"), ("CARE", "CARE"),
    ("INDIA RATING", "IRRPL"), ("BRICKWORK", "BRCK"),
    ("ACUITE", "ACUITE"), ("INFOMERICS", "IVRPL"),
]


def _short_agency(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    upper = name.upper()
    for key, short in _AGENCY_SHORT:
        if key in upper:
            return short
    return name.split()[0].upper()


def _clean(value) -> Optional[str]:
    text = (value or "").strip()
    return text if text and text != "-" else None


# CRISIL RatingDocs URLs embed company name and publish date, e.g.
# .../RatingDocs/SammaanCapitalLimited_April%209_%202026_RR_....html
_CRISIL_DOC_RE = re.compile(
    r"RatingDocs/([A-Za-z]+)_([A-Za-z]+)(?:%20|\s)(\d{1,2})_(?:%20|\s)(\d{4})")
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}


def _condense(text: Optional[str]) -> str:
    return re.sub(r"[^a-z]", "", (text or "").lower())


def _suspect_press_release(link: str, issuer_name: Optional[str],
                           event_date: Optional[date]) -> bool:
    """True when a link provably belongs to another company's report.

    Agencies sometimes attach the wrong press release to NSDL's feed (seen
    live: Sammaan Capital's 2025 "New" rating linking a Bharti Telecom
    report published 48 days later). Only CRISIL-style URLs are checkable.
    Keep the link if the embedded company slug matches the issuer. Renames
    make slug-only checks unsafe (Sammaan was Indiabulls, so old reports
    legitimately carry the old name) — for mismatched slugs, keep only
    reports published on/before the event: a rationale always exists before
    the rating is entered, while wrongly attached links are later documents.
    """
    m = _CRISIL_DOC_RE.search(link or "")
    if not m:
        return False  # opaque URL (e.g. ICRA report IDs) — can't validate
    slug, month_name, day, year = m.groups()
    slug_c, issuer_c = _condense(slug), _condense(issuer_name)
    if issuer_c and (slug_c[:8] in issuer_c or issuer_c[:8] in slug_c):
        return False
    month = _MONTHS.get(month_name.lower())
    if month and event_date:
        try:
            url_date = date(int(year), month, int(day))
        except ValueError:
            return bool(issuer_c)
        if url_date <= event_date + timedelta(days=7):
            return False
    elif not issuer_c:
        return False  # nothing to validate against — keep
    return True


def _press_release(row: Dict, when: Optional[date]) -> Optional[str]:
    link = _clean(row.get("pressReleaseLink"))
    if link and _suspect_press_release(link, row.get("issuerName"), when):
        return None
    return link


def parse_ratings(payload: Dict) -> List[Dict]:
    """Current ratings from the credit-ratings payload, one per agency.

    Each entry: agency (short code), rating, outlook, action (e.g. Upgrade),
    changed_at (ISO date), prev_rating (latest earlier rating by the same
    agency, when published), press_release (the agency's own rationale).
    """
    earlier: Dict[str, List] = {}
    for row in payload.get("earlierRatings") or []:
        rating = _clean(row.get("currentRating"))
        agency = row.get("creditRatingAgencyName")
        when = parse_date(_clean(row.get("ratingChangeDate"))
                          or _clean(row.get("creditRatingDate")) or "")
        if rating and agency and when:
            earlier.setdefault(agency, []).append((when, rating))
    out = []
    for row in payload.get("currentRatings") or []:
        rating = _clean(row.get("currentRating"))
        if not rating:
            continue
        agency_raw = row.get("creditRatingAgencyName")
        changed = parse_date(_clean(row.get("ratingChangeDate")) or "")
        event = changed or parse_date(_clean(row.get("creditRatingDate")) or "")
        prev_list = sorted(earlier.get(agency_raw, []), reverse=True)
        out.append({
            "agency": _short_agency(agency_raw),
            "rating": rating,
            "outlook": _clean(row.get("outlook")),
            "action": _clean(row.get("ratingAction")),
            "changed_at": changed.isoformat() if changed else None,
            "prev_rating": prev_list[0][1] if prev_list else None,
            "press_release": _press_release(row, event),
        })
    return out


def parse_rating_history(payload: Dict) -> List[Dict]:
    """Every dated rating event (current + earlier) from the credit-ratings
    payload, newest first. NSDL keeps the full per-agency trail (rating,
    outlook and action changes), each with its own press release — persisted
    locally so the history survives even if NSDL ever truncates it.
    """
    out = []
    for key in ("currentRatings", "earlierRatings"):
        for row in payload.get(key) or []:
            rating = _clean(row.get("currentRating"))
            if not rating:
                continue
            changed = parse_date(_clean(row.get("ratingChangeDate"))
                                 or _clean(row.get("creditRatingDate")) or "")
            out.append({
                "agency": _short_agency(row.get("creditRatingAgencyName")),
                "rating": rating,
                "outlook": _clean(row.get("outlook")),
                "action": _clean(row.get("ratingAction")),
                "changed_at": changed.isoformat() if changed else None,
                "press_release": _press_release(row, changed),
            })
    out.sort(key=lambda x: x["changed_at"] or "", reverse=True)
    return out


def fetch_ratings(isin: str, timeout: int = 30) -> List[Dict]:
    """Fetch current agency ratings for one ISIN. Raises on HTTP errors."""
    return fetch_rating_data(isin, timeout)[0]


def fetch_rating_data(isin: str, timeout: int = 30) -> Tuple[List[Dict], List[Dict]]:
    """Fetch (current ratings, full rating history) for one ISIN in one call."""
    resp = requests.get(RATINGS_URL, params={"isin": isin}, headers=HEADERS,
                        timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    return parse_ratings(payload), parse_rating_history(payload)
