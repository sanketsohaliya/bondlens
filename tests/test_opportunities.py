"""Opportunity alerts: pure evaluation logic (no DB, no network)."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend import opportunities as opp
from backend.analytics import valuation
from backend.analytics.schedule import Flow, Schedule

TODAY = date(2026, 10, 5)


def plain_bond(face=1000.0, coupon=120.0, years=1) -> Schedule:
    end = TODAY + timedelta(days=365 * years)
    return Schedule("INE000TEST01", "plain",
                    [Flow("interest", end, amount=coupon), Flow("redemption", end, amount=face)],
                    face_value=face, redemption_matches_face=True, periods_per_year=1,
                    day_count="act/365")


# --- filters ------------------------------------------------------------------

def test_rules_keep_bond_descriptors_and_refuse_trade_history():
    filters = {"min_rating": "BBB-", "min_years": 0.5, "sector": "", "traded_within": 5,
               "min_ytm": 5, "payout": "bullet", "secured_only": False}
    assert opp.static_filters(filters) == {"min_rating": "BBB-", "min_years": 0.5, "payout": "bullet"}
    assert opp.rejected_filters(filters) == ["min_ytm", "traded_within"]
    assert opp.rejected_filters({"traded_within": None}) == []


# --- yield -> price -------------------------------------------------------------

def test_price_for_yield_inverts_the_explorer_valuation():
    sched = plain_bond()
    for target in (8.0, 12.0, 20.0):
        price = opp.price_for_yield(sched, {}, target, TODAY)
        assert price is not None
        ytm = valuation.calculate(price, sched, {}, TODAY)["ytm"]
        assert ytm is not None and abs(ytm - target) < 0.02
        # A cheaper ask yields more: the threshold is an upper bound on price.
        cheaper = valuation.calculate(price - 5, sched, {}, TODAY)["ytm"]
        assert cheaper is not None and cheaper > target


def test_price_for_yield_is_none_when_unpriceable_or_out_of_range():
    assert opp.price_for_yield(Schedule("X", "empty"), {}, 10.0, TODAY) is None
    assert opp.price_for_yield(plain_bond(), {}, -50.0, TODAY) is None  # no price yields -50%
    assert opp.price_for_yield(plain_bond(), {}, float("nan"), TODAY) is None


def test_schedule_fingerprint_changes_with_inputs_only():
    a = opp.schedule_fingerprint({"face_value": 1000}, [{"amount": 1}])
    assert a == opp.schedule_fingerprint({"face_value": 1000}, [{"amount": 1}])
    assert a != opp.schedule_fingerprint({"face_value": 1000}, [{"amount": 2}])
    assert a != opp.schedule_fingerprint({"face_value": 100}, [{"amount": 1}])


# --- thresholds -----------------------------------------------------------------

RULE = {"id": 7, "name": "BBB 12%", "enabled": True, "min_ask_ytm": 12.0,
        "max_ticket": 200_000, "max_price": None, "min_qty": None}


def test_build_thresholds_uses_cached_prices_and_reports_missing():
    prices = {("A", 12.0): 950.0, ("B", 12.0): None}
    th, missing = opp.build_thresholds([RULE], {7: ["A", "B", "C"]}, [], prices)
    assert missing == {("C", 12.0)}
    [a] = th.asks["A"]
    assert (a.rule_id, a.max_ask_price, a.max_ticket, a.min_qty) == (7, 950.0, 200_000, 1.0)
    assert "B" not in th.asks      # unpriceable and no max_price fallback
    assert "C" not in th.asks      # held back until solved: no false positives


def test_build_thresholds_keeps_a_price_only_rule_for_unpriceable_bonds():
    rule = {**RULE, "max_price": 1000.0}
    th, _ = opp.build_thresholds([rule], {7: ["B"]}, [], {("B", 12.0): None})
    [b] = th.asks["B"]
    assert b.max_ask_price is None and b.max_price == 1000.0


def test_build_thresholds_for_exit_targets_takes_the_higher_floor():
    targets = [{"isin": "H", "enabled": True, "min_bid_price": 1010.0, "max_bid_ytm": 9.0}]
    th, missing = opp.build_thresholds([], {}, targets, {("H", 9.0): 1030.0})
    assert th.bids["H"].min_bid_price == 1030.0 and missing == set()
    th, missing = opp.build_thresholds([], {}, targets, {})
    assert "H" not in th.bids and missing == {("H", 9.0)}


# --- tick matching ----------------------------------------------------------------

def test_best_levels_ignore_empty_slots():
    asks = [{"price": 0, "qty": 0}, {"price": 1010.0, "qty": 5}, {"price": 1005.0, "qty": 2},
            {"price": 990.0, "qty": None}]
    assert opp.best_ask(asks) == (1005.0, 2)
    bids = [{"price": 980.0, "qty": 1}, {"price": 995.0, "qty": 10}, {"price": None, "qty": 3}]
    assert opp.best_bid(bids) == (995.0, 10)
    assert opp.best_ask([]) is None and opp.best_bid(None) is None  # type: ignore[arg-type]


def test_match_ask_applies_every_condition():
    r = opp.AskRule(rule_id=1, name="r", max_ask_price=950.0, max_price=None,
                    max_ticket=10_000.0, min_qty=2.0)
    assert opp.match_ask([r], 940.0, 5) == [r]
    assert opp.match_ask([r], 960.0, 5) == []       # yield too low (price too high)
    assert opp.match_ask([r], 940.0, 1) == []       # one unit, min_qty 2
    assert opp.match_ask([r], 940.0, 20) == []      # ticket 18,800 > 10,000
    bare = opp.AskRule(rule_id=2, name="x", max_ask_price=None, max_price=None,
                       max_ticket=None, min_qty=1.0)
    assert opp.match_ask([bare], 1.0, 1) == []      # nothing to compare: never fires


def test_match_bid():
    assert opp.match_bid(opp.BidTarget(min_bid_price=1000.0), 1000.0)
    assert not opp.match_bid(opp.BidTarget(min_bid_price=1000.0), 999.0)
    assert not opp.match_bid(None, 5000.0)


def hit(**kw) -> opp.Hit:
    base = {"source": "rule:7", "isin": "A", "side": "ask", "price": 950.0, "qty": 4,
            "exchange": "BSE", "rule_name": "BBB 12%", "ytm": 12.4}
    return opp.Hit(**{**base, **kw})


def test_should_fire_once_a_day_unless_the_quote_improves_materially():
    first = hit()
    assert opp.should_fire(None, first)
    assert not opp.should_fire(first, hit(ytm=12.5))          # +0.1 pt: same opportunity
    assert opp.should_fire(first, hit(ytm=12.65, price=940))  # +0.25 pt: better, re-alert
    assert not opp.should_fire(first, hit(ytm=12.0, price=960))
    # Bid side: price up by >= 0.5 %.
    bid = hit(side="bid", source="target", price=1000.0, ytm=None)
    assert not opp.should_fire(bid, hit(side="bid", source="target", price=1004.0, ytm=None))
    assert opp.should_fire(bid, hit(side="bid", source="target", price=1005.0, ytm=None))


def test_render_names_the_opportunity_and_links_to_the_order_book():
    bond = {"issuer_name": "DEV ACCELERATOR LIMITED", "nse_symbol": None, "bse_scrip_id": "1175DAL29",
            "credit_rating": "ACUITE: BBB/Stable", "maturity_date": date(2029, 8, 1)}
    at = datetime(2026, 10, 5, 4, 1, 41, tzinfo=ZoneInfo("UTC"))  # 09:31:41 IST
    title, body, url = opp.render(hit(), bond, domain="bondlens.in", tick_at=at)
    assert title == "12.40% @ ₹950.00 × 4 — DEV ACCELERATOR LIMITED"
    assert "1175DAL29 · A" in body and "ACUITE: BBB/Stable" in body
    assert "matures Aug 2029" in body and "ticket ₹3,800" in body
    assert "rule: BBB 12%" in body and body.endswith("as of 09:31:41 IST")
    assert url == "https://bondlens.in/?bond=A&tab=order%20book"
    title, body, _ = opp.render(hit(side="bid", source="target", price=1015.0, qty=10, ytm=None,
                                    rule_name=None), bond, domain="x")
    assert title == "Bid ₹1,015.00 × 10 for your 1175DAL29" and "exit target" in body


@pytest.mark.parametrize("value,expected", [("12", 12.0), (None, None), ("x", None),
                                            (float("inf"), None)])
def test_float_coercion(value, expected):
    assert opp._f(value) == expected


def test_shortfall_only_when_the_exact_yield_misses_the_rule():
    assert opp.shortfall(hit(ytm=15.24, ytm_bound=15.0)) is None
    assert opp.shortfall(hit(ytm=15.0, ytm_bound=15.0)) is None
    assert opp.shortfall(hit(ytm=14.995, ytm_bound=15.0)) is None      # within a basis point
    assert opp.shortfall(hit(ytm=14.7, ytm_bound=15.0)) == 0.3
    assert opp.shortfall(hit(ytm=None, ytm_bound=15.0)) is None
    assert opp.shortfall(hit(ytm=14.7, ytm_bound=None)) is None
    # Bids are bounded from above.
    assert opp.shortfall(hit(side="bid", ytm=8.5, ytm_bound=9.0)) is None
    assert opp.shortfall(hit(side="bid", ytm=9.4, ytm_bound=9.0)) == 0.4


def test_thresholds_carry_the_rule_yield_for_the_recheck():
    th, _ = opp.build_thresholds([RULE], {7: ["A"]}, [], {("A", 12.0): 950.0})
    assert th.asks["A"][0].min_ask_ytm == 12.0
    targets = [{"isin": "H", "enabled": True, "min_bid_price": None, "max_bid_ytm": 9.0}]
    th, _ = opp.build_thresholds([], {}, targets, {("H", 9.0): 1030.0})
    assert th.bids["H"].max_bid_ytm == 9.0
