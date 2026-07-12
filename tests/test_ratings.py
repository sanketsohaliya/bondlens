"""Rating logic tests over bond_ratings-shaped rows."""
from datetime import date, timedelta

from bondlens.analytics import ratings

TODAY = date(2026, 7, 12)


def test_grade_extraction():
    assert ratings.grade("AAA") == "AAA"
    assert ratings.grade("CARE AA+ (CE)") == "AA+"
    assert ratings.grade("IND AA-") == "AA-"
    assert ratings.grade("BBB+") == "BBB+"
    assert ratings.grade("D") == "D"
    assert ratings.grade(None) is None
    assert ratings.grade("Withdrawn") is None


def _rank(grade: str) -> int:
    r = ratings.rank(grade)
    assert r is not None
    return r


def test_rank_ordering():
    assert _rank("AAA") < _rank("AA+") < _rank("AA")
    assert _rank("BBB-") < _rank("BB+")
    assert _rank("D") == max(ratings.RATING_RANK.values())


def test_best_rank_across_agencies():
    cur = [{"rating": "AA"}, {"rating": "AA+"}, {"rating": None}]
    assert ratings.best_rank(cur) == ratings.RATING_RANK["AA+"]


def test_composite():
    cur = [{"rating": "AAA", "outlook": "Stable"},
           {"rating": "AA+", "outlook": "No Outlook"}]
    assert ratings.composite(cur) == "AAA/Stable, AA+"
    assert ratings.composite([]) is None


def test_recent_downgrade_detected():
    hist = [
        {"agency": "CRISIL", "rating": "AA", "rating_date": TODAY - timedelta(days=400),
         "rating_action": "Reaffirm"},
        {"agency": "CRISIL", "rating": "AA-", "rating_date": TODAY - timedelta(days=10),
         "rating_action": "Downgrade"},
    ]
    ch = ratings.recent_change(hist, TODAY)
    assert ch is not None
    assert ch["direction"] == "down"
    assert ch["prev_rating"] == "AA"


def test_old_change_ignored():
    hist = [
        {"agency": "CRISIL", "rating": "AA", "rating_date": TODAY - timedelta(days=400),
         "rating_action": "Reaffirm"},
        {"agency": "CRISIL", "rating": "AA-", "rating_date": TODAY - timedelta(days=90),
         "rating_action": "Downgrade"},
    ]
    assert ratings.recent_change(hist, TODAY) is None


def test_downgrade_wins_over_upgrade():
    hist = [
        {"agency": "ICRA", "rating": "AA", "rating_date": TODAY - timedelta(days=60),
         "rating_action": None},
        {"agency": "ICRA", "rating": "AA+", "rating_date": TODAY - timedelta(days=5),
         "rating_action": "Upgrade"},
        {"agency": "CARE", "rating": "AA", "rating_date": TODAY - timedelta(days=60),
         "rating_action": None},
        {"agency": "CARE", "rating": "AA-", "rating_date": TODAY - timedelta(days=3),
         "rating_action": "Downgrade"},
    ]
    ch = ratings.recent_change(hist, TODAY)
    assert ch is not None and ch["direction"] == "down"


def test_direction_from_grades_when_action_missing():
    hist = [
        {"agency": "CRISIL", "rating": "A+", "rating_date": TODAY - timedelta(days=200),
         "rating_action": None},
        {"agency": "CRISIL", "rating": "AA-", "rating_date": TODAY - timedelta(days=7),
         "rating_action": None},
    ]
    ch = ratings.recent_change(hist, TODAY)
    assert ch is not None and ch["direction"] == "up"


def test_in_default():
    assert ratings.in_default([{"rating": "D"}])
    assert ratings.in_default([{"rating": "CRISIL D"}])
    assert not ratings.in_default([{"rating": "AAA"}])
    assert not ratings.in_default([])


def test_timeline_grouped_newest_first():
    hist = [
        {"agency": "CARE", "rating": "AA", "rating_date": date(2024, 1, 1)},
        {"agency": "CARE", "rating": "AA-", "rating_date": date(2025, 1, 1)},
        {"agency": "ICRA", "rating": "AA", "rating_date": date(2024, 6, 1)},
    ]
    tl = ratings.timeline(hist)
    assert set(tl) == {"CARE", "ICRA"}
    assert tl["CARE"][0]["rating"] == "AA-"  # newest first
