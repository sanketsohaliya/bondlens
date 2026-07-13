"""Rating logic tests over bond_ratings-shaped rows."""
from datetime import date, timedelta

from backend.analytics import ratings

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
    cur = [{"agency": "ICRA", "rating": "AAA", "outlook": "Stable"},
           {"agency": "CRISIL", "rating": "AA+", "outlook": "No Outlook"}]
    assert ratings.composite(cur) == "ICRA: AAA/Stable, CRISIL: AA+"
    assert ratings.composite([]) is None


def test_composite_dedupes_and_keeps_disagreements():
    cur = [{"agency": "CRISIL", "rating": "AA+", "outlook": "Stable"},
           {"agency": "CRISIL", "rating": "AA+", "outlook": "Stable"},
           {"agency": "ICRA", "rating": "AA", "outlook": "Negative"}]
    assert ratings.composite(cur) == "CRISIL: AA+/Stable, ICRA: AA/Negative"


def test_composite_without_agency():
    assert ratings.composite([{"rating": "AA"}]) == "AA"


def test_latest_downgrade_detected():
    hist = [
        {"agency": "CRISIL", "rating": "AA", "rating_date": TODAY - timedelta(days=400),
         "rating_action": "Reaffirm"},
        {"agency": "CRISIL", "rating": "AA-", "rating_date": TODAY - timedelta(days=10),
         "rating_action": "Downgrade"},
    ]
    ch = ratings.latest_change(hist)
    assert ch is not None
    assert ch["direction"] == "down"
    assert ch["prev_rating"] == "AA"


def test_old_change_still_reported():
    # No recency window: the latest movement counts however old it is.
    hist = [
        {"agency": "CRISIL", "rating": "AA", "rating_date": TODAY - timedelta(days=400),
         "rating_action": "Reaffirm"},
        {"agency": "CRISIL", "rating": "AA-", "rating_date": TODAY - timedelta(days=90),
         "rating_action": "Downgrade"},
    ]
    ch = ratings.latest_change(hist)
    assert ch is not None and ch["direction"] == "down"


def test_never_moved_reports_none():
    hist = [
        {"agency": "CRISIL", "rating": "AA", "rating_date": TODAY - timedelta(days=400),
         "rating_action": "Reaffirm"},
        {"agency": "CRISIL", "rating": "AA", "rating_date": TODAY - timedelta(days=90),
         "rating_action": "Reaffirm"},
    ]
    assert ratings.latest_change(hist) is None


def test_most_recent_action_wins():
    hist = [
        {"agency": "ICRA", "rating": "AA", "rating_date": TODAY - timedelta(days=60),
         "rating_action": None},
        {"agency": "ICRA", "rating": "AA+", "rating_date": TODAY - timedelta(days=3),
         "rating_action": "Upgrade"},
        {"agency": "CARE", "rating": "AA", "rating_date": TODAY - timedelta(days=60),
         "rating_action": None},
        {"agency": "CARE", "rating": "AA-", "rating_date": TODAY - timedelta(days=5),
         "rating_action": "Downgrade"},
    ]
    ch = ratings.latest_change(hist)
    assert ch is not None and ch["direction"] == "up"


def test_downgrade_then_reaffirms_reports_the_downgrade():
    # 962APSB32A shape: AA+(CE) reaffirms, a downgrade to AA(CE), then
    # yearly reaffirms at the new grade. The downgrade is the last
    # movement even though reaffirms follow it.
    hist = [
        {"agency": "ACUITE", "rating": "AA+(CE)", "rating_date": date(2023, 1, 5),
         "rating_action": "Reaffirm"},
        {"agency": "ACUITE", "rating": "AA+(CE)", "rating_date": date(2023, 7, 4),
         "rating_action": "Reaffirm"},
        {"agency": "ACUITE", "rating": "AA(CE)", "rating_date": date(2023, 12, 8),
         "rating_action": "Downgrade"},
        {"agency": "ACUITE", "rating": "AA(CE)", "rating_date": date(2024, 12, 6),
         "rating_action": "Reaffirm"},
        {"agency": "ACUITE", "rating": "AA(CE)", "rating_date": date(2026, 1, 14),
         "rating_action": "Reaffirm"},
    ]
    ch = ratings.latest_change(hist)
    assert ch is not None
    assert ch["direction"] == "down"
    assert ch["prev_rating"] == "AA+(CE)"
    assert ch["rating_date"] == date(2023, 12, 8)


def test_duplicate_rows_do_not_fabricate_changes():
    # Re-ingested copies of the same event must not register as movement
    # or displace the true previous rating.
    hist = [
        {"agency": "CRISIL", "rating": "AA", "rating_date": date(2024, 3, 1),
         "rating_action": "Reaffirm"},
        {"agency": "CRISIL", "rating": "AA", "rating_date": date(2024, 3, 1),
         "rating_action": "Reaffirm"},
        {"agency": "CRISIL", "rating": "AA", "rating_date": date(2024, 3, 1),
         "rating_action": "Reaffirm"},
    ]
    assert ratings.latest_change(hist) is None


def test_direction_from_grades_when_action_missing():
    hist = [
        {"agency": "CRISIL", "rating": "A+", "rating_date": TODAY - timedelta(days=200),
         "rating_action": None},
        {"agency": "CRISIL", "rating": "AA-", "rating_date": TODAY - timedelta(days=7),
         "rating_action": None},
    ]
    ch = ratings.latest_change(hist)
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
