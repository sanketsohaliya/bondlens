"""Unit tests for backend.nsdl_parse (pure parsers, no DB)."""
import unittest
from datetime import date

from backend.nsdl_parse import (
    agency_norm,
    clean,
    coupon_rate_num,
    dual_rates,
    fix_record_date,
    parse_coupondetail,
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

    def test_dual_rates(self):
        assert dual_rates("(7.04/7.54)%") == (7.04, 7.54)
        assert dual_rates("(7.4/7.9)%") == (7.4, 7.9)
        assert dual_rates("8.25") is None
        assert dual_rates("RESET RATE - 8%") is None
        assert dual_rates(None) is None

    def test_coupon_rate_num(self):
        assert coupon_rate_num("8.25") == 8.25
        assert coupon_rate_num("9%") == 9.0
        assert coupon_rate_num("8.4 %") == 8.4
        assert coupon_rate_num("(7.04/7.54)%") == 7.04
        assert coupon_rate_num("Nifty 50 Index") is None
        assert coupon_rate_num("-") is None
        assert coupon_rate_num(None) is None

    def test_coupon_rate_num_prose(self):
        # Fixed-rate prose (real nsdl_raw values) -> the rate.
        assert coupon_rate_num("8.83% (REFER REMARKS)") == 8.83
        assert coupon_rate_num("8.50% (Compounded annually)") == 8.5
        assert coupon_rate_num("12% (REFER REMARKS)") == 12.0
        # Category-rate prose -> lowest plausible (secondary-buyer) rate.
        assert coupon_rate_num("Cat I & II-8.12% Cat-III-8.32%") == 8.12
        assert coupon_rate_num("7.04% (ADDITIONAL 0.50% TO RIs)") == 7.04
        assert coupon_rate_num("8.58% FOR CATEGORY I, II AND III") == 8.58
        assert coupon_rate_num(
            "7.04 CATEGORY I.II& III, 7.54 FOR CATEGORY- IV") == 7.04
        assert coupon_rate_num(
            "8.1% (cat I & II and 0.2% additn int for cat III)") == 8.1
        assert coupon_rate_num(
            "7.53% (IF ON RECORD DATE BONDS HELD BY RII, ELSE 7.28% p.a)"
        ) == 7.28
        # Floating / MLD / step-up / vague -> None.
        assert coupon_rate_num("RESET RATE - 10%. HOLDER AND ISSUER...") is None
        assert coupon_rate_num("3 Month TBill") is None
        assert coupon_rate_num("SBI 3 Month MCLR") is None
        assert coupon_rate_num("NA (EFFECTIVE YIELD 10%)") is None
        assert coupon_rate_num("15% XIRR AND 7.26 GSEC LINKED") is None
        assert coupon_rate_num(
            "13.6% UPTO MARCH 31, 2023 13.7% FROM APRIL 1, 2023, TO "
            "MARCH 31, 2027 16% FROM APRIL 1, 2027, TO OCTOBER 31, 2047"
        ) is None
        assert coupon_rate_num("As per term sheet") is None
        assert coupon_rate_num("na") is None

    def test_parse_coupondetail_dual_rate_summed_amounts(self):
        # The INE134E07364 shape: NSDL publishes interest as the SUM of
        # both category rates (70.4 + 75.4 = 145.8 per ₹1000 face).
        payload = {"coupensVo": {
            "couponDetails": {
                "couponRate": "(7.04/7.54)%",
                "couponType": "Simple",
                "interestPaymentFrequency": "ANNUALLY ON 28 MARCH",
            },
            "cashFlowScheduleDetails": {"cashFlowSchedule": [
                {"cashFlowsEvent": "Interest", "dueDate": "28-03-2027",
                 "amountPayable": 145.8},
                {"cashFlowsEvent": "Full Redemption",
                 "dueDate": "28-03-2028", "amountPayable": 1000},
            ]},
        }}
        out = parse_coupondetail("INE134E07364", payload)
        assert out["_terms"][0]["coupon_rate"] == 7.04
        interest = [f for f in out["bond_cashflows"]
                    if f["event"] == "Interest"]
        assert interest[0]["amount_payable"] == 70.4

    def test_parse_coupondetail_dual_rate_correct_amounts_untouched(self):
        # The INE363O07046 shape: amounts already at the base rate.
        payload = {"coupensVo": {
            "couponDetails": {"couponRate": "(7.17/7.67)%"},
            "cashFlowScheduleDetails": {"cashFlowSchedule": [
                {"cashFlowsEvent": "Interest", "dueDate": "16-11-2026",
                 "amountPayable": 71.7},
                {"cashFlowsEvent": "Full Redemption",
                 "dueDate": "16-11-2027", "amountPayable": 1000},
            ]},
        }}
        out = parse_coupondetail("INE363O07046", payload)
        assert out["_terms"][0]["coupon_rate"] == 7.17
        interest = [f for f in out["bond_cashflows"]
                    if f["event"] == "Interest"]
        assert interest[0]["amount_payable"] == 71.7

    def test_parse_coupondetail_plain_rate_unaffected(self):
        payload = {"coupensVo": {
            "couponDetails": {"couponRate": "8.25"},
            "cashFlowScheduleDetails": {"cashFlowSchedule": [
                {"cashFlowsEvent": "Interest", "dueDate": "01-01-2027",
                 "amountPayable": 82.5},
            ]},
        }}
        out = parse_coupondetail("INE000TEST01", payload)
        assert out["_terms"][0]["coupon_rate"] == 8.25
        assert out["bond_cashflows"][0]["amount_payable"] == 82.5

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
