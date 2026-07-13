"""Unit tests for backend.nsdl_parse (pure parsers, no DB)."""
import unittest
from datetime import date

from backend.nsdl_parse import (
    agency_norm,
    clean,
    fix_record_date,
    parse_credit_ratings,
    parse_holdings,
    parse_instruments,
    parse_redemptions,
    to_date,
    to_int,
    to_num,
)


class TestNsdlParse(unittest.TestCase):

    def test_clean_null_markers(self):
        for junk in (None, "", "-", "NA", "None", "Not applicable"):
            assert clean(junk) is None
        assert clean(" AAA ") == "AAA"
        assert clean(0) == "0"  # numeric zero is real data

    def test_to_date_formats(self):
        assert to_date("03-04-2020") == date(2020, 4, 3)
        assert to_date("30/06/2026") == date(2026, 6, 30)
        assert to_date("-") is None
        assert to_date("Expiry of 60 months") is None  # free text

    def test_fix_record_date(self):
        # Normal: record before due passes through untouched.
        assert fix_record_date(date(2026, 7, 9), date(2026, 7, 24)) \
            == date(2026, 7, 9)
        assert fix_record_date(None, date(2026, 7, 24)) is None
        # Day/month swap: 07-09 was really 09-07 (the 9EFSL27 case).
        assert fix_record_date(date(2026, 9, 7), date(2026, 7, 24)) \
            == date(2026, 7, 9)
        # Wrong year: 12 Aug 2026 for a 27 Aug 2025 payment.
        assert fix_record_date(date(2026, 8, 12), date(2025, 8, 27)) \
            == date(2025, 8, 12)
        # Unrepairable (no swap/year fix lands 0-60d before due) -> None.
        assert fix_record_date(date(2026, 10, 5), date(2025, 1, 20)) is None
        # Swap exists but lands outside the window -> falls through to None.
        assert fix_record_date(date(2026, 12, 8), date(2025, 8, 27)) is None

    def test_to_num_and_int(self):
        assert to_num("1,000.5") == 1000.5
        assert to_num("-") is None
        assert to_num("abc") is None
        assert to_int("27") == 27
        assert to_int(None) is None

    def test_agency_norm_unifies_variants(self):
        assert agency_norm("CARE RATINGS LIMITED") == "CARE"
        assert agency_norm("CARE Ratings Limited") == "CARE"
        assert agency_norm("CRISIL RATINGS LIMITED") == "CRISIL"
        assert agency_norm(
            "INDIA RATING AND RESEARCH PVT. LTD") == "INDIA RATINGS"
        assert agency_norm("-") is None

    def test_parse_credit_ratings_current_and_earlier(self):
        payload = {
            "currentRatings": [{
                "creditRatingAgencyName": "CRISIL RATINGS LIMITED",
                "currentRating": "AAA", "outlook": "Stable",
                "creditRatingDate": "01-10-2018", "ratingAction": "-",
                "iciWatch": "-", "ratingChangeDate": "-",
                "verificationStatus": "Verified",
                "pressReleaseLink": None,
            }],
            "earlierRatings": [{
                "creditRatingAgencyName": "CARE Ratings Limited",
                "currentRating": "AA+", "outlook": "Negative",
                "creditRatingDate": "05-10-2016",
            }],
        }
        rows = parse_credit_ratings("INE000TEST01", payload)["bond_ratings"]
        assert len(rows) == 2
        cur, old = rows
        assert cur["is_current"] and cur["agency"] == "CRISIL"
        assert cur["rating"] == "AAA"
        assert cur["rating_date"] == date(2018, 10, 1)
        assert cur["rating_action"] is None
        assert not old["is_current"] and old["agency"] == "CARE"

    def test_parse_instruments_terms_and_further_issues(self):
        payload = {"instrumentsVo": {
            "instruments": {
                "faceValue": "100000.0", "allotmentDate": "03-04-2020",
                "redemptionDate": "02-03-2030",
                "perpetualInNature": "No", "secured": "Secured",
                "tenureYears": "9", "series": "N.A",
            },
            "assetCover": {"assetCvge": "Principal + Interest",
                           "assetCvrPercent": "2.57"},
            "furtherIssue": {"furtherIssueVo": [
                {"allotmentDate": "12-02-2025",
                 "allotmentQuantity": "86000",
                 "issuePriceRs": "100000.000"},
                {"allotmentDate": "-", "allotmentQuantity": "-",
                 "issuePriceRs": "-"},  # all-null row must be dropped
            ]},
        }}
        out = parse_instruments("INE000TEST01", payload)
        terms = out["_terms"][0]
        assert terms["face_value"] == 100000.0
        assert terms["redemption_date"] == date(2030, 3, 2)
        assert terms["perpetual"] is False
        assert terms["series"] is None          # 'N.A' -> null
        assert terms["asset_cover_percent"] == 2.57
        assert len(out["bond_further_issues"]) == 1

    def test_parse_redemptions_events_filtered(self):
        payload = {
            "redemptionType": "Partial Redemption By Face Value",
            "netQuantity": "19100",
            "putOption": {"specifiedDates": "Expiry of 60 months"},
            "callOption": {},
            "redemption": [
                {"valueRedeemed": "2233333.0",
                 "redemptionMethod": "Face Value",
                 "reasonForRedemption": "Maturity",
                 "partialRedemptionDates": "30-04-2023"},
                {"valueRedeemed": None, "redemptionMethod": None,
                 "reasonForRedemption": None,
                 "partialRedemptionDates": "-"},
            ],
        }
        out = parse_redemptions("INE000TEST01", payload)
        assert (out["_terms"][0]["put_specified_dates"]
                == "Expiry of 60 months")
        events = out["bond_redemption_events"]
        assert len(events) == 1
        assert events[0]["redemption_date"] == date(2023, 4, 30)

    def test_parse_holdings_skips_null_matrix(self):
        payload = {
            "reportGenerationDate": "30/06/2026",
            "shareholdingData": [{
                "headerName": "B1) Institutions (Domestic)",
                "headerEmployeeCount": "3",
                "totalShareCountHeader": "42000",
                "listOfSubHeader": [
                    {"subHeaderName": "Mutual Funds - B1a",
                     "subHeaderEmployeeCount": "3",
                     "totalShareCountSubHeader": "42000"},
                    {"subHeaderName": "Empty Cat",
                     "subHeaderEmployeeCount": None,
                     "totalShareCountSubHeader": None},
                ],
            }],
        }
        rows = parse_holdings("INE000TEST01", "June 26",
                              payload)["bond_holdings"]
        # header row + one non-null subheader; all-null one skipped
        assert len(rows) == 2
        assert rows[0]["subheader"] is None and rows[0]["quantity"] == 42000
        assert rows[1]["subheader"] == "Mutual Funds - B1a"
        assert rows[1]["report_date"] == date(2026, 6, 30)


if __name__ == "__main__":
    unittest.main()
