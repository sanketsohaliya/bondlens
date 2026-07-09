"""Tests for parsing NSDL's credit-ratings payload (bondlens.nsdl.parse_ratings)."""
import unittest

from bondlens.nsdl import parse_ratings, _short_agency


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

    def test_unknown_agency_first_word(self):
        self.assertEqual(_short_agency("Acme Ratings Co"), "ACME")
        self.assertIsNone(_short_agency(None))


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
