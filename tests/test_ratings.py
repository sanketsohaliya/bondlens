"""Tests for parsing NSDL's credit-ratings payload (bondlens.nsdl.parse_ratings)."""
import unittest

from datetime import date

from bondlens.nsdl import (parse_listing, parse_rating_history, parse_ratings,
                           _short_agency, _suspect_press_release)


class ParseListing(unittest.TestCase):
    def test_prefers_nse_entry_and_parses_dates(self):
        payload = {"listingDetails": [
            {"exchangeName": "BSE", "allotmentDate": "15-01-2026",
             "listingDate": "19-01-2026", "issueType": "First Issue"},
            {"exchangeName": "NSE", "allotmentDate": "16-01-2026",
             "listingDate": "20-01-2026", "issueType": "First Issue"},
        ]}
        out = parse_listing(payload)
        self.assertEqual(out["allotment_date"], "2026-01-16")
        self.assertEqual(out["listing_date"], "2026-01-20")
        self.assertEqual(out["issue_type"], "First Issue")

    def test_empty_payload(self):
        out = parse_listing({})
        self.assertIsNone(out["allotment_date"])
        self.assertIsNone(out["listing_date"])


class SuspectPressRelease(unittest.TestCase):
    CRISIL = ("https://www.crisilratings.com/mnt/winshare/Ratings/RatingList/"
              "RatingDocs/{}_RR_371016.html")

    def test_wrong_company_and_date_dropped(self):
        # Seen live: Sammaan Capital's 2025-05-30 "New" event linking a
        # Bharti Telecom report dated 2025-07-17.
        link = self.CRISIL.format("BhartiTelecomLimited_July%2017_%202025")
        self.assertTrue(_suspect_press_release(
            link, "SAMMAAN CAPITAL LIMITED", date(2025, 5, 30)))

    def test_matching_company_kept(self):
        link = self.CRISIL.format("SammaanCapitalLimited_April%209_%202026")
        self.assertFalse(_suspect_press_release(
            link, "SAMMAAN CAPITAL LIMITED", date(2020, 1, 1)))

    def test_renamed_issuer_kept_when_report_precedes_event(self):
        # Seen live for INE148I07TX1: the "New" event (2024-07-09) links a
        # report named for the pre-rename company, published before the
        # event — legitimate, must be kept.
        link = self.CRISIL.format("IndiabullsHousingFinanceLimited_May%2031_%202024")
        self.assertFalse(_suspect_press_release(
            link, "SAMMAAN CAPITAL LIMITED", date(2024, 7, 9)))

    def test_mismatched_name_report_after_event_dropped(self):
        link = self.CRISIL.format("IndiabullsHousingFinanceLimited_May%2031_%202024")
        self.assertTrue(_suspect_press_release(
            link, "SOME OTHER COMPANY LIMITED", date(2024, 3, 1)))

    def test_opaque_urls_never_suspect(self):
        self.assertFalse(_suspect_press_release(
            "https://www.icra.in/Rationale/ShowRationaleReport?Id=140856",
            "SAMMAAN CAPITAL LIMITED", date(2025, 5, 30)))

    def test_no_issuer_no_date_kept(self):
        link = self.CRISIL.format("BhartiTelecomLimited_July%2017_%202025")
        self.assertFalse(_suspect_press_release(link, None, None))

    def test_parse_drops_suspect_link(self):
        row = cr("CRISIL RATINGS LIMITED", "AA", changed="30-05-2025",
                 link=self.CRISIL.format("BhartiTelecomLimited_July%2017_%202025"))
        row["issuerName"] = "SAMMAAN CAPITAL LIMITED"
        out = parse_rating_history({"earlierRatings": [row]})
        self.assertIsNone(out[0]["press_release"])


def cr(agency, rating, outlook="Stable", action="Upgrade",
       changed="09-04-2026", link="https://example.com/rr.html"):
    return {
        "creditRatingAgencyName": agency, "currentRating": rating,
        "outlook": outlook, "ratingAction": action,
        "ratingChangeDate": changed, "pressReleaseLink": link,
    }


class ShortAgency(unittest.TestCase):
    def test_known_agencies(self):
        self.assertEqual(_short_agency("CRISIL RATINGS LIMITED"), "CRISIL")
        self.assertEqual(_short_agency("ICRA LIMITED"), "ICRA")
        self.assertEqual(_short_agency("CARE RATINGS LIMITED"), "CARE")
        self.assertEqual(_short_agency("India Ratings and Research Pvt Ltd"), "IRRPL")
        # NSDL's actual (singular) spelling, seen live:
        self.assertEqual(_short_agency("INDIA RATING AND RESEARCH PVT. LTD"), "IRRPL")

    def test_unknown_agency_first_word(self):
        self.assertEqual(_short_agency("Acme Ratings Co"), "ACME")
        self.assertIsNone(_short_agency(None))


class ParseRatingHistory(unittest.TestCase):
    def test_current_and_earlier_merged_newest_first(self):
        # Shape observed live for INE530B07310 (IIFL Finance): both agencies'
        # current ratings plus the dated earlier trail, each with its own
        # press release.
        payload = {
            "currentRatings": [
                cr("ICRA LIMITED", "AA", outlook="Negative",
                   action="Reaffirm", changed="11-02-2026"),
            ],
            "earlierRatings": [
                cr("ICRA LIMITED", "AA", outlook="Stable",
                   action="Reaffirm", changed="25-09-2024",
                   link="https://example.com/old.html"),
                cr("CRISIL RATINGS LIMITED", "AA", outlook="Positive",
                   action="Reaffirm", changed="20-11-2023"),
            ],
        }
        out = parse_rating_history(payload)
        self.assertEqual([x["changed_at"] for x in out],
                         ["2026-02-11", "2024-09-25", "2023-11-20"])
        self.assertEqual(out[1]["press_release"], "https://example.com/old.html")
        self.assertEqual(out[2]["agency"], "CRISIL")

    def test_undated_events_kept_last(self):
        payload = {
            "earlierRatings": [
                {"creditRatingAgencyName": "CRISIL RATINGS LIMITED",
                 "currentRating": "AA", "outlook": "Stable",
                 "ratingAction": "New", "ratingChangeDate": "-",
                 "creditRatingDate": "-", "pressReleaseLink": "-"},
                cr("ICRA LIMITED", "AA", changed="01-08-2023"),
            ],
        }
        out = parse_rating_history(payload)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["changed_at"], "2023-08-01")
        self.assertIsNone(out[1]["changed_at"])
        self.assertEqual(out[1]["action"], "New")

    def test_falls_back_to_credit_rating_date(self):
        payload = {
            "earlierRatings": [
                {"creditRatingAgencyName": "ICRA LIMITED",
                 "currentRating": "AA", "outlook": "Stable",
                 "ratingAction": "Reaffirm", "ratingChangeDate": "-",
                 "creditRatingDate": "23-12-2022", "pressReleaseLink": "-"},
            ],
        }
        out = parse_rating_history(payload)
        self.assertEqual(out[0]["changed_at"], "2022-12-23")

    def test_blank_ratings_skipped_and_empty_payload(self):
        payload = {"currentRatings": [cr("ICRA LIMITED", "-")]}
        self.assertEqual(parse_rating_history(payload), [])
        self.assertEqual(parse_rating_history({}), [])


class ParseRatings(unittest.TestCase):
    def test_current_ratings_parsed(self):
        # Shape observed live for INE244L07168 (Sammaan Finserve): three
        # agencies, all upgraded to AA+ in Apr-May 2026 while NSE still
        # showed the 2018 at-issuance AAA.
        payload = {
            "instrumentRateFlag": "Rated",
            "currentRatings": [
                cr("CRISIL RATINGS LIMITED", "AA+"),
                cr("ICRA LIMITED", "AA+", changed="20-05-2026"),
            ],
            "earlierRatings": [],
        }
        out = parse_ratings(payload)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["agency"], "CRISIL")
        self.assertEqual(out[0]["rating"], "AA+")
        self.assertEqual(out[0]["outlook"], "Stable")
        self.assertEqual(out[0]["action"], "Upgrade")
        self.assertEqual(out[0]["changed_at"], "2026-04-09")
        self.assertEqual(out[0]["press_release"], "https://example.com/rr.html")
        self.assertEqual(out[1]["changed_at"], "2026-05-20")

    def test_prev_rating_from_earlier_latest_wins(self):
        payload = {
            "currentRatings": [cr("CRISIL RATINGS LIMITED", "AA+")],
            "earlierRatings": [
                cr("CRISIL RATINGS LIMITED", "AAA", changed="16-08-2018"),
                cr("CRISIL RATINGS LIMITED", "AA", changed="30-06-2020"),
                cr("ICRA LIMITED", "AA-", changed="01-01-2021"),  # other agency
            ],
        }
        out = parse_ratings(payload)
        self.assertEqual(out[0]["prev_rating"], "AA")  # latest CRISIL, not AAA

    def test_blanks_and_dashes_skipped(self):
        payload = {
            "currentRatings": [
                cr("CRISIL RATINGS LIMITED", "-"),
                cr("ICRA LIMITED", ""),
                {"creditRatingAgencyName": "CARE RATINGS LIMITED",
                 "currentRating": "A+", "outlook": "-", "ratingAction": None,
                 "ratingChangeDate": "-", "pressReleaseLink": "-"},
            ],
        }
        out = parse_ratings(payload)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["rating"], "A+")
        self.assertIsNone(out[0]["outlook"])
        self.assertIsNone(out[0]["action"])
        self.assertIsNone(out[0]["changed_at"])
        self.assertIsNone(out[0]["press_release"])

    def test_unrated_payload(self):
        self.assertEqual(parse_ratings({"instrumentRateFlag": "Unrated"}), [])
        self.assertEqual(parse_ratings({}), [])


if __name__ == "__main__":
    unittest.main()
