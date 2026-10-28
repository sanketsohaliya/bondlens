"""Regression tests for the explorer data-quality fixes:
secured mapping, perpetual YTC, YTM reliability flag, rating dedupe."""
from datetime import date, timedelta

import pytest

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
    monkeypatch.setattr(queries, "exchange_notice_rows", lambda isins=None, **k: {})

    rows = {r["isin"]: r for r in compute.build_rows(today=TODAY)}
    assert rows["INETECH00001"]["tech_delay"] is True
    assert rows["INETECH00001"]["in_default"] is False
    assert rows["INEDFLT00002"]["in_default"] is True
    assert rows["INEDFLT00002"]["tech_delay"] is False


# --- Kite deep link is precomputed onto the explorer row ----------------------

def test_kite_url_is_resolved_once_per_bond_preferring_the_retail_exchange(monkeypatch):
    from backend import kite

    core = {
        "INEKITE00001": {"isin": "INEKITE00001", "on_nse": True, "on_bse": True,
                         "nse_symbol": "ABC", "nse_series": "N1", "bse_scrip_id": "ABC27",
                         "retail_trade": {"close": 1000, "trade_date": TODAY, "exchange": "BSE"}},
        "INEKITE00002": {"isin": "INEKITE00002", "on_nse": True, "on_bse": False,
                         "nse_symbol": "NOPE", "nse_series": None},
    }
    calls = []

    def fake_kite_url(nse_symbol, nse_series, bse_scrip_id=None, prefer=None):
        calls.append((nse_symbol, nse_series, bse_scrip_id, prefer))
        return "https://kite.zerodha.com/x" if nse_symbol == "ABC" else None

    monkeypatch.setattr(kite, "kite_url", fake_kite_url)
    monkeypatch.setattr(queries, "explorer_core", lambda: core)
    monkeypatch.setattr(queries, "all_cashflows", lambda: {})
    monkeypatch.setattr(queries, "cashflows_for", lambda isins: {})
    monkeypatch.setattr(queries, "all_terms", lambda: {})
    monkeypatch.setattr(queries, "current_ratings", lambda: {})
    monkeypatch.setattr(queries, "rating_history", lambda: {})
    monkeypatch.setattr(queries, "defaulted_isins", lambda: set())
    monkeypatch.setattr(queries, "tech_delay_isins", lambda: set())
    monkeypatch.setattr(queries, "exchange_notice_rows", lambda isins=None, **k: {})

    rows = {r["isin"]: r for r in compute.build_rows(today=TODAY)}
    assert rows["INEKITE00001"]["kite_url"] == "https://kite.zerodha.com/x"
    assert rows["INEKITE00002"]["kite_url"] is None
    assert ("ABC", "N1", "ABC27", "BSE") in calls
    assert ("NOPE", None, None, None) in calls
    assert len(calls) == 2


@pytest.fixture
def pricing_case(monkeypatch):
    from backend import app as api_module
    from backend import db

    isin = "INE000TEST01"

    class FixedDate(date):
        @classmethod
        def today(cls):
            return TODAY

    monkeypatch.setattr(api_module, "date", FixedDate)
    monkeypatch.setattr(api_module.kite, "kite_url", lambda *args, **kwargs: None)
    monkeypatch.setattr(queries, "current_ratings", lambda: {})
    monkeypatch.setattr(queries, "rating_history", lambda *args: {})
    monkeypatch.setattr(queries, "ratings_for_isins", lambda *args: [])
    monkeypatch.setattr(queries, "defaulted_isins", lambda: set())
    monkeypatch.setattr(queries, "tech_delay_isins", lambda: set())
    monkeypatch.setattr(queries, "exchange_notice_rows", lambda isins=None, **k: {})
    monkeypatch.setattr(queries, "bond_row", lambda _: {"isin": isin, "issuer_name": None})
    monkeypatch.setattr(queries, "bond_trustee", lambda _: None)
    monkeypatch.setattr(queries, "bond_extras", lambda _: {"defaults": [], "technical_defaults": []})
    monkeypatch.setattr(db, "get_portfolio_lots", lambda _: [{
        "id": 1, "isin": isin, "units": 1, "price": 1000,
        "purchase_date": TODAY - timedelta(days=1), "platform": None}])

    def run(flows, overrides=None):
        terms = {"coupon_rate": 10, "face_value": 1000,
                 "redemption_date": TODAY + timedelta(days=365),
                 "interest_payment_frequency": "Annual", "day_count_convention": "Actual/365",
                 **(overrides or {})}
        core = {isin: {"isin": isin, "on_nse": True, "on_bse": False,
                       **terms, "retail_trade": {"close": 1000, "trade_date": TODAY, "exchange": "NSE"}}}
        monkeypatch.setattr(queries, "explorer_core", lambda: core)
        monkeypatch.setattr(queries, "all_terms", lambda: {isin: terms})
        monkeypatch.setattr(queries, "all_cashflows", lambda: {isin: flows})
        monkeypatch.setattr(queries, "cashflows_for", lambda _: {isin: flows})
        monkeypatch.setattr(queries, "bond_terms", lambda _: terms)
        monkeypatch.setattr(queries, "bond_cashflows", lambda _: flows)
        row = compute.build_rows(TODAY)[0]
        monkeypatch.setattr(queries, "analytics_row", lambda *args: row)
        detail = api_module.bond_detail(isin, price=None)
        book = {"nse": {"asks": [{"price": 1000, "qty": 1}]}, "bse": None, "ytm_at_ask": []}
        api_module._annotate_ytm(book, isin)
        portfolio = api_module.portfolio(user_id=1)
        return row, detail, book, portfolio

    return run


@pytest.mark.parametrize("problem", ["missing_amount", "missing_periods", "unsupported_daycount"])
def test_invalid_schedules_cannot_reappear_as_fallback_or_portfolio_yields(pricing_case, problem):
    first = TODAY + timedelta(days=365)
    end = first if problem != "missing_periods" else TODAY + timedelta(days=1095)
    flows = [{"event": "Interest", "due_date": first, "amount_payable": None if problem == "missing_amount" else 100},
             {"event": "Redemption", "due_date": end, "amount_redeem": 1000}]
    terms: dict = {"redemption_date": end}
    if problem == "unsupported_daycount":
        terms["day_count_convention"] = "Actual/Actual (ICMA)"
    row, detail, book, portfolio = pricing_case(flows, terms)
    assert row["ytm"] is None and row["ytm_method"] is None
    assert detail["ytm"] is None and detail["ytm_approx"] is None
    assert detail["schedule_warnings"]
    if problem != "unsupported_daycount":
        assert row["payout_total_snapshot"] is None and detail["future_total_per_unit"] is None
    assert row["duration"] is None and detail["duration"] is None
    assert all(entry["ytm"] is None for entry in book["ytm_at_ask"])
    assert portfolio["positions"][0]["yield_on_cost"] is None


def test_call_premium_is_not_silently_priced_as_par(pricing_case):
    end = TODAY + timedelta(days=365)
    flows = [{"event": "Interest", "due_date": end, "amount_payable": 100},
             {"event": "Redemption", "due_date": end, "amount_redeem": 1000}]
    row, detail, _, _ = pricing_case(flows, {
        "perpetual": True, "redemption_date": date(9999, 12, 31),
        "call_indicator": "Yes", "call_specified_dates": end.strftime("%d %B, %Y"),
        "redemption_premium_details": "2% premium"})
    assert row["ytm"] is None and detail["ytm"] is None
    assert detail["schedule_warnings"]


def test_unresolved_call_terms_remain_visible_without_claiming_ytw(pricing_case):
    end = TODAY + timedelta(days=365)
    flows = [{"event": "Interest", "due_date": end, "amount_payable": 100},
             {"event": "Redemption", "due_date": end, "amount_redeem": 1000}]
    row, detail, _, _ = pricing_case(flows, {
        "call_indicator": "Yes", "call_specified_dates": "Any time after five years from allotment"})
    assert row["ytm"] == detail["ytm"] == 10.0
    assert detail["yield_to_worst"] is None
    assert any("call" in w.lower() and "unresolved" in w.lower() for w in detail["schedule_warnings"])


@pytest.mark.parametrize("convention", ["Actual/Actual", None])
def test_clean_act_act_schedule_has_no_warnings(pricing_case, convention):
    end = TODAY + timedelta(days=365)
    flows = [{"event": "Interest", "due_date": end, "amount_payable": 100},
             {"event": "Redemption", "due_date": end, "amount_redeem": 1000}]
    _, detail, _, _ = pricing_case(flows, {"day_count_convention": convention})
    assert detail["schedule_warnings"] == []
    assert detail["schedule_usable"] is True


def test_perpetual_ytc_is_not_presented_as_maturity_ytm_or_full_ytw(pricing_case):
    end = TODAY + timedelta(days=365)
    flows = [{"event": "Interest", "due_date": end, "amount_payable": 100},
             {"event": "Redemption", "due_date": end, "amount_redeem": 1000}]
    row, detail, book, portfolio = pricing_case(flows, {
        "perpetual": True, "redemption_date": date(9999, 12, 31),
        "call_indicator": "Yes", "call_specified_dates": end.strftime("%d %B, %Y")})
    assert row["ytm"] == detail["ytm"] == 10.0 and row["ytm_method"] == "ytc"
    assert detail["ytm_exact"] is None and detail["yield_to_worst"] is None
    assert len(book["ytm_at_ask"]) == 1
    assert book["ytm_at_ask"][0]["ytm"] == 10
    assert book["ytm_at_ask"][0]["yield_type"] == "ytc"
    assert portfolio["positions"][0]["yield_on_cost"] is None


def test_combined_terminal_payment_preserves_totals_and_exposes_provenance(pricing_case):
    end = TODAY + timedelta(days=365)
    flows = [{"event": "Interest", "due_date": TODAY, "amount_payable": 100},
             {"event": "Interest", "due_date": end, "amount_payable": 1100}]
    row, detail, _, _ = pricing_case(flows, {"redemption_type": "Full Redemption"})
    assert row["ytm"] == detail["ytm"] == 10
    assert row["ytm_method"] == "exact"
    assert row["payout_total_snapshot"] == detail["future_total_per_unit"] == 1100
    final = [f for f in detail["cashflows"] if f["due_date"] == end]
    assert len(final) == 2
    assert all(not f["estimated"] and f["adjustments"][0]["kind"] == "combined_payment_split" for f in final)


def test_historical_estimates_do_not_label_current_yield_as_estimated(pricing_case):
    end = TODAY + timedelta(days=365)
    flows = [{"event": "Interest", "due_date": TODAY - timedelta(days=730), "amount_payable": None},
             {"event": "Interest", "due_date": TODAY - timedelta(days=365), "amount_payable": 100},
             {"event": "Interest", "due_date": TODAY, "amount_payable": 100},
             {"event": "Interest", "due_date": end, "amount_payable": 100},
             {"event": "Redemption", "due_date": end, "amount_redeem": 1000}]
    row, detail, _, _ = pricing_case(flows, {"allotment_date": TODAY - timedelta(days=1095)})
    assert row["ytm_method"] == "exact"
    assert detail["amounts_estimated"] is True
    assert detail["yield_amounts_estimated"] is False
    assert detail["cashflows"][0]["adjustments"][0]["kind"] == "coupon_estimated"


def test_portfolio_exposes_effective_annual_market_yield(pricing_case):
    from backend.analytics.schedule import _months_back

    end = TODAY + timedelta(days=365)
    flows = [{"event": "Interest", "due_date": _months_back(end, n), "amount_payable": 8.5}
             for n in reversed(range(12))]
    flows.append({"event": "Redemption", "due_date": end, "amount_redeem": 1000})
    row, _, _, portfolio = pricing_case(flows, {"interest_payment_frequency": "Monthly", "coupon_rate": 10.2})
    position = portfolio["positions"][0]
    assert row["ytm"] is not None
    assert position["ytm_effective"] == pytest.approx(((1 + row["ytm"] / 100 / 12) ** 12 - 1) * 100, abs=1e-9)
    assert position["yield_on_cost_notes"]


def test_optional_cashflows_are_disclosed_and_excluded_in_api(pricing_case):
    old, maturity = TODAY - timedelta(days=365), TODAY + timedelta(days=365)
    flows = [{"event": "Interest", "due_date": old, "amount_payable": 100},
             {"event": "Full Redemption", "due_date": old, "amount_payable": 1000},
             {"event": "Interest", "due_date": maturity, "amount_payable": 200},
             {"event": "Full Redemption", "due_date": maturity, "amount_payable": 1000}]
    _, detail, _, portfolio = pricing_case(flows, {"interest_payment_frequency": "Cumulative"})
    assert sum(f["optional"] for f in detail["cashflows"]) == 2
    assert all(not f["future"] for f in detail["cashflows"] if f["optional"])
    assert any("optional" in note.lower() for note in portfolio["positions"][0]["yield_on_cost_notes"])
