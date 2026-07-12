"""Credit-rating logic over the parsed bond_ratings table.

bond_ratings holds one row per (isin, agency, rating event): current rows
carry is_current = true; the rest form the per-agency historical trail.
Agencies use a shared grade scale (AAA..D) with +/- modifiers and
occasional prefixes ("CARE AAA", "CRISIL AA+ (CE)", "IND AA") — the parser
strips agency prefixes but suffixes like (CE)/(SO) ride along.
"""
import itertools
import re
from datetime import date, timedelta

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


def grade(rating: str | None) -> str | None:
    """Extract the canonical grade token from a raw rating string."""
    if not rating:
        return None
    m = _GRADE_RE.search(rating.upper())
    return m.group(1) if m else None


def rank(rating: str | None) -> int | None:
    """Rank of a single raw rating string (0 = AAA = best)."""
    g = grade(rating)
    return RATING_RANK.get(g) if g else None


def best_rank(current: list[dict]) -> int | None:
    """Best (lowest) rank across an ISIN's current agency ratings."""
    ranks = [r for r in (rank(x.get("rating")) for x in current)
             if r is not None]
    return min(ranks) if ranks else None


def composite(current: list[dict]) -> str | None:
    """Display string across agencies, one entry per agency with its name
    ('CRISIL AA+/Stable, ICRA AA/Negative'). Best grade first; entries
    without a parseable grade sort last. Duplicate (agency, rating)
    pairs collapse to one."""
    if not current:
        return None
    seen: set[tuple[str, str]] = set()
    parts: list[tuple[str, str]] = []
    for x in current:
        s = x.get("rating") or ""
        if not s:
            continue
        if x.get("outlook") and x["outlook"].lower() not in ("no outlook", ""):
            s += f"/{x['outlook']}"
        agency = (x.get("agency") or "").strip()
        if (agency, s) not in seen:
            seen.add((agency, s))
            parts.append((agency, s))
    parts.sort(key=lambda p: (rank(p[1]) is None, rank(p[1]) or 0, p[0]))
    return ", ".join(f"{a} {s}" if a else s for a, s in parts) or None


def recent_change(history: list[dict],
                  today: date | None = None) -> dict | None:
    """Most significant rating action inside the flag window.

    Returns {'direction': 'up'|'down', 'agency', 'rating', 'prev_rating',
    'rating_date'} or None. Downgrades win over upgrades when both exist
    (risk first). Direction comes from the actual grade movement per
    agency; the action text is only a fallback.
    """
    today = today or date.today()
    cutoff = today - timedelta(days=FLAG_DAYS)
    by_agency: dict[str, list[dict]] = {}
    for h in history:
        if h.get("agency"):
            by_agency.setdefault(h["agency"], []).append(h)

    changes = []
    for agency, rows in by_agency.items():
        dated = sorted((r for r in rows if r.get("rating_date")),
                       key=lambda r: r["rating_date"])
        for prev, cur in itertools.pairwise(dated):
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


def in_default(current: list[dict]) -> bool:
    """Any agency currently rates the bond D."""
    return any(grade(x.get("rating")) in _DEFAULT_GRADES for x in current)


def timeline(history: list[dict]) -> dict[str, list[dict]]:
    """Per-agency rating trail, newest first, for the detail view."""
    out: dict[str, list[dict]] = {}
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
