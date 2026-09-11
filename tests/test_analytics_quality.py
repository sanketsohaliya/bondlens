"""Regression tests for the explorer data-quality fixes:
secured mapping, perpetual YTC, YTM reliability flag, rating dedupe."""
from datetime import date

from backend import queries
from backend.analytics import compute, ratings, yields
from backend.analytics.compute import _yn, ytm_reliability
from backend.analytics.schedule import Flow, Schedule

TODAY = date(2026, 7, 12)


# --- secured / tax_free mapping -------------------------------------------

def test_yn_recognizes_nsdl_secured_values():
    assert _yn("Secured") is True
    assert _yn("Unsecured") is False
    assert _yn("SECURED") is True
    assert _yn("unsecured") is False


def test_yn_still_handles_yes_no():
    assert _yn("Yes") is True
    assert _yn("No") is False
    assert _yn(None) is None
    assert _yn("Maybe") is None


# --- rating composite: one entry per agency ---------------------------------

def _cur(*triples):
    return [{"agency": a, "rating": r, "outlook": o} for a, r, o in triples]


def test_composite_names_every_agency():
    cur = _cur(("CARE", "AAA", "Stable"), ("CRISIL", "AAA", "Stable"),
               ("INDIA RATINGS", "AAA", "Stable"))
    assert ratings.composite(cur) == (
        "CARE: AAA/Stable, CRISIL: AAA/Stable, INDIA RATINGS: AAA/Stable")


def test_composite_keeps_disagreements_visible_best_first():
    cur = _cur(("ICRA", "AA", "Negative"), ("CRISIL", "AA+", "Stable"))
    assert ratings.composite(cur) == "CRISIL: AA+/Stable, ICRA: AA/Negative"


def test_composite_no_outlook_and_single():
    assert ratings.composite(_cur(("CARE", "AAA", None))) == "CARE: AAA"
    assert ratings.composite(_cur(("CARE", "AA", "No Outlook"))) == "CARE: AA"
    assert ratings.composite([]) is None


# --- YTM reliability ---------------------------------------------------------

def test_reliability_normal_band():
    far = date(2030, 1, 1)
    assert ytm_reliability(8.5, far, TODAY) is True
    assert ytm_reliability(-0.5, far, TODAY) is True
    assert ytm_reliability(65.0, far, TODAY) is False
    assert ytm_reliability(-27.8, far, TODAY) is False


def test_reliability_tightens_near_maturity():
    near = date(2026, 9, 1)  # < 90 days out
    assert ytm_reliability(15.0, near, TODAY) is True
    assert ytm_reliability(35.0, near, TODAY) is False   # ok far, not near
    assert ytm_reliability(-0.5, near, TODAY) is False   # ok far, not near


def test_reliability_none_cases():
    assert ytm_reliability(None, date(2030, 1, 1), TODAY) is None
    assert ytm_reliability(8.0, None, TODAY) is True  # perpetual YTC w/ bounds


def test_reliability_floating_basis():
    # Real DB values: 'Variable-Others', 'Variable - Index Linked', etc.
    far = date(2030, 1, 1)
    for basis in ("Variable-Others", "Variable - Index Linked",
                  "Variable - Mibor Linked", "Variable- Commodity linked"):
        assert ytm_reliability(8.5, far, TODAY, basis) is False
    assert ytm_reliability(8.5, far, TODAY, "Fixed Interest") is True
    assert ytm_reliability(None, far, TODAY, "Variable-Others") is None


# --- perpetual YTC -----------------------------------------------------------

def _perp_schedule() -> Schedule:
    flows = [Flow(event="interest", due_date=date(2026 + i, 12, 1),
                  amount=9.0) for i in range(10)]
    return Schedule(isin="TESTPERP", shape="perpetual", flows=flows,
                    face_value=100.0, frequency="annual", periods_per_year=1)


def test_yield_to_call_prices_to_the_call():
    sched = _perp_schedule()
    ytc = yields.yield_to_call(100.0, sched, [date(2028, 12, 1)], today=TODAY)
    assert ytc is not None
    # Dirty price 100 mid-coupon (buyer keeps the full near coupon), so the
    # yield sits somewhat above the 9% coupon.
    assert 9.0 < ytc < 13.0


def test_yield_to_call_none_without_dates_or_price():
    sched = _perp_schedule()
    assert yields.yield_to_call(100.0, sched, [], today=TODAY) is None
    assert yields.yield_to_call(None, sched, [date(2028, 12, 1)],
                                today=TODAY) is None


def test_parse_call_dates_moved_helper():
    terms = {"call_indicator": "Yes",
             "call_specified_dates": "call on 15-09-2027 and 15-Sep-2029"}
    assert yields.parse_call_dates(terms) == [date(2027, 9, 15),
                                              date(2029, 9, 15)]
    assert yields.parse_call_dates({"call_indicator": "No"}) == []
    assert yields.parse_call_dates(None) == []


def test_parse_call_dates_prose_style():
    # Real feed shape: 'THE CALL OPTION PAYMENT DATES ARE NOVEMBER 24, 2026,
    # AND ANNUALLY THEREAFTER ON NOVEMBER 24, 2027 ...'
    terms = {"call_indicator": "Yes",
             "call_specified_dates":
                 "CALL OPTION PAYMENT DATES ARE NOVEMBER 24, 2026, AND "
                 "ANNUALLY THEREAFTER ON NOVEMBER 24, 2027"}
    assert yields.parse_call_dates(terms) == [date(2026, 11, 24),
                                              date(2027, 11, 24)]
    terms = {"call_indicator": "Yes",
             "call_specified_dates":
                 "AT THE END OF 36 MONTHS FROM DEEMED DATE OF ALLOTMENT "
                 "I.E. DECEMBER 17, 2024. OPTION AVAILABLE TO THE COMPANY"}
    assert yields.parse_call_dates(terms) == [date(2024, 12, 17)]
    # Pure prose with no concrete date -> nothing extracted.
    terms = {"call_indicator": "Yes",
             "call_specified_dates":
                 "AFTER THE EXPIRY OF 5 (FIVE) YEARS FROM THE DEEMED DATE "
                 "OF ALLOTMENT UNLESS NOT PERMITTED UNDER APPLICABLE LAW"}
    assert yields.parse_call_dates(terms) == []


# --- technical delays are a soft flag, not defaults --------------------------

def test_tech_delay_does_not_brand_default(monkeypatch):
    # An IIFL-Samasta-style bond: AA-, one investor-side payment hiccup.
    # It must surface with tech_delay=True but in_default=False (a real
    # 0.0002% technical delay used to hide the bond from the explorer).
    core = {
        "INETECH00001": {"isin": "INETECH00001", "on_nse": True,
                         "on_bse": False},
        "INEDFLT00002": {"isin": "INEDFLT00002", "on_nse": True,
                         "on_bse": False},
    }
    monkeypatch.setattr(queries, "explorer_core", lambda: core)
    monkeypatch.setattr(queries, "all_cashflows", lambda: {})
    monkeypatch.setattr(queries, "cashflows_for", lambda isins: {})
    monkeypatch.setattr(queries, "all_terms", lambda: {})
    monkeypatch.setattr(queries, "current_ratings", lambda: {})
    monkeypatch.setattr(queries, "rating_history", lambda: {})
    monkeypatch.setattr(queries, "defaulted_isins",
                        lambda: {"INEDFLT00002"})
    monkeypatch.setattr(queries, "tech_delay_isins",
                        lambda: {"INETECH00001"})

    rows = {r["isin"]: r for r in compute.build_rows(today=TODAY)}
    assert rows["INETECH00001"]["tech_delay"] is True
    assert rows["INETECH00001"]["in_default"] is False
    assert rows["INEDFLT00002"]["in_default"] is True
    assert rows["INEDFLT00002"]["tech_delay"] is False
