"""Credit-rating logic over the parsed bond_ratings table.

bond_ratings holds one row per (isin, agency, rating event): current rows
carry is_current = true; the rest form the per-agency historical trail.
Agencies use a shared grade scale (AAA..D) with +/- modifiers and
occasional prefixes ("CARE AAA", "CRISIL AA+ (CE)", "IND AA") — the parser
strips agency prefixes but suffixes like (CE)/(SO) ride along.
"""
import re
from datetime import date, timedelta
from typing import Dict, List, Optional

# Best (lowest index) to worst.
RATING_ORDER = [
    "AAA", "AA+", "AA", "AA-", "A+", "A", "A-",
    "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-", "B+", "B", "B-",
    "C+", "C", "C-", "D",
]
RATING_RANK = {r: i for i, r in enumerate(RATING_ORDER)}

# How long an upgrade/downgrade marker stays visible.
FLAG_DAYS = 30

_GRADE_RE = re.compile(
    r"(?<![A-Z])(AAA|AA\+|AA-|AA|A\+|A-|BBB\+|BBB-|BBB|BB\+|BB-|BB|B\+|B-|"
    r"C\+|C-|D|C|A|B)(?![A-Z])")

_DOWN_WORDS = ("downgrade", "lowered", "negative implication")
_UP_WORDS = ("upgrade", "raised")
_WITHDRAWN_WORDS = ("withdraw",)
_DEFAULT_GRADES = {"D"}


def grade(rating: Optional[str]) -> Optional[str]:
    """Extract the canonical grade token from a raw rating string."""
    if not rating:
        return None
    m = _GRADE_RE.search(rating.upper())
    return m.group(1) if m else None


def rank(rating: Optional[str]) -> Optional[int]:
    """Rank of a single raw rating string (0 = AAA = best)."""
    g = grade(rating)
    return RATING_RANK.get(g) if g else None


def best_rank(current: List[Dict]) -> Optional[int]:
    """Best (lowest) rank across an ISIN's current agency ratings."""
    ranks = [r for r in (rank(x.get("rating")) for x in current)
             if r is not None]
    return min(ranks) if ranks else None


def composite(current: List[Dict]) -> Optional[str]:
    """Display string across agencies: 'AAA/Stable (CRISIL, ICRA)' style
    kept simple — one entry per agency, joined."""
    if not current:
        return None
    parts = []
    for x in current:
        s = x.get("rating") or ""
        if x.get("outlook") and x["outlook"].lower() not in ("no outlook", ""):
            s += f"/{x['outlook']}"
        parts.append(s)
    return ", ".join(p for p in parts if p) or None


def recent_change(history: List[Dict],
                  today: Optional[date] = None) -> Optional[Dict]:
    """Most significant rating action inside the flag window.

    Returns {'direction': 'up'|'down', 'agency', 'rating', 'prev_rating',
    'rating_date'} or None. Downgrades win over upgrades when both exist
    (risk first). Direction comes from the actual grade movement per
    agency; the action text is only a fallback.
    """
    today = today or date.today()
    cutoff = today - timedelta(days=FLAG_DAYS)
    by_agency: Dict[str, List[Dict]] = {}
    for h in history:
        if h.get("agency"):
            by_agency.setdefault(h["agency"], []).append(h)

    changes = []
    for agency, rows in by_agency.items():
        dated = sorted((r for r in rows if r.get("rating_date")),
                       key=lambda r: r["rating_date"])
        for prev, cur in zip(dated, dated[1:]):
            if cur["rating_date"] < cutoff:
                continue
            pr, cr = rank(prev.get("rating")), rank(cur.get("rating"))
            direction = None
            if pr is not None and cr is not None and pr != cr:
                direction = "down" if cr > pr else "up"
            else:
                action = (cur.get("rating_action") or "").lower()
                if any(w in action for w in _DOWN_WORDS):
                    direction = "down"
                elif any(w in action for w in _UP_WORDS):
                    direction = "up"
            if direction:
                changes.append({
                    "direction": direction, "agency": agency,
                    "rating": cur.get("rating"),
                    "prev_rating": prev.get("rating"),
                    "rating_date": cur["rating_date"],
                })
    if not changes:
        return None
    downs = [c for c in changes if c["direction"] == "down"]
    pool = downs or changes
    return max(pool, key=lambda c: c["rating_date"])


def in_default(current: List[Dict]) -> bool:
    """Any agency currently rates the bond D."""
    return any(grade(x.get("rating")) in _DEFAULT_GRADES for x in current)


def timeline(history: List[Dict]) -> Dict[str, List[Dict]]:
    """Per-agency rating trail, newest first, for the detail view."""
    out: Dict[str, List[Dict]] = {}
    for h in sorted(history,
                    key=lambda r: (r.get("rating_date") or date.min),
                    reverse=True):
        agency = h.get("agency") or "?"
        out.setdefault(agency, []).append({
            "rating": h.get("rating"),
            "outlook": h.get("outlook"),
            "action": h.get("rating_action"),
            "rating_date": h.get("rating_date"),
            "press_release": h.get("press_release"),
        })
    return out
