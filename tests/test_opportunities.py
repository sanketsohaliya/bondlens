"""Opportunity alerts: pure evaluation logic (no DB, no network)."""
from datetime import UTC, date, datetime, timedelta
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

RULE = {"id": 7, "enabled": True, "min_ask_ytm": 12.0, "filters": {"min_rating": "BBB-"},
        "min_ticket": 50_000}


def test_build_thresholds_uses_cached_prices_and_reports_missing():
    prices = {("A", 12.0): 950.0, ("B", 12.0): None}
    th, missing = opp.build_thresholds([RULE], {7: ["A", "B", "C"]}, [], prices)
    assert missing == {("C", 12.0)}
    [a] = th.asks["A"]
    assert (a.rule_id, a.max_ask_price, a.min_ticket) == (7, 950.0, 50_000)
    assert "B" not in th.asks      # unpriceable: skipped
    assert "C" not in th.asks      # held back until solved: no false positives


def test_build_thresholds_skips_a_rule_without_a_yield():
    th, missing = opp.build_thresholds([{**RULE, "min_ask_ytm": None}], {7: ["A"]}, [], {})
    assert th.asks == {} and missing == set()


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
    r = opp.AskRule(rule_id=1, label="r", max_ask_price=950.0, min_ticket=5_000.0)
    assert opp.match_ask([r], 940.0, 6) == [r]      # 5,640 on offer >= 5,000
    assert opp.match_ask([r], 960.0, 6) == []       # yield too low (price too high)
    assert opp.match_ask([r], 940.0, 5) == []       # ticket 4,700 < 5,000
    loose = opp.AskRule(rule_id=3, label="l", max_ask_price=950.0, min_ticket=None)
    assert opp.match_ask([loose], 940.0, 1) == [loose]
    bare = opp.AskRule(rule_id=2, label="x", max_ask_price=None, min_ticket=None)
    assert opp.match_ask([bare], 1.0, 1) == []      # unpriceable: never fires


def test_match_bid():
    assert opp.match_bid(opp.BidTarget(min_bid_price=1000.0), 1000.0)
    assert not opp.match_bid(opp.BidTarget(min_bid_price=1000.0), 999.0)
    assert not opp.match_bid(None, 5000.0)


def hit(**kw) -> opp.Hit:
    base = {"source": "rule:7", "isin": "A", "side": "ask", "price": 950.0, "qty": 4,
            "exchange": "BSE", "rule_label": "Ask YTM ≥ 12%, Rating ≥ BBB-", "ytm": 12.4}
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


def test_render_reads_issuer_then_yield_one_fact_per_line():
    bond = {"issuer_name": "DEV ACCELERATOR LIMITED", "nse_symbol": None, "bse_scrip_id": "1175DAL29",
            "credit_rating": "ACUITE: BBB/Stable", "maturity_date": date(2029, 8, 1)}
    at = datetime(2026, 10, 5, 4, 1, 41, tzinfo=ZoneInfo("UTC"))  # 09:31:41 IST
    title, body, url = opp.render(hit(), bond, domain="bondlens.in", tick_at=at)
    assert title == "DEV ACCELERATOR LIMITED — 12.40%"
    assert body.split("\n") == [
        "₹950 × 4 = ₹3,800 on BSE",
        "ACUITE BBB/Stable, matures Aug 2029",
        "1175DAL29, A",
        "Rule: Ask YTM ≥ 12%, Rating ≥ BBB-",
        "09:31:41 IST",
    ]
    assert url == "https://bondlens.in/?bond=A&tab=order%20book"
    # Hot path: yield unknown yet; fractional price keeps paise; unrated bond.
    title, body, _ = opp.render(hit(ytm=None, price=1314.8, qty=83),
                                {"issuer_name": "ICL", "maturity_date": date(2031, 6, 1)},
                                domain="x", tick_at=at)
    assert title == "ICL — ask ₹1,314.80"
    assert body.split("\n")[:3] == ["₹1,314.80 × 83 = ₹1,09,128.40 on BSE".replace("₹1,09,128.40", f"₹{1314.8 * 83:,.2f}"),
                                     "matures Jun 2031", "A"]
    # Bid side.
    title, body, _ = opp.render(hit(side="bid", source="target", price=1015.0, qty=10, ytm=8.9,
                                    rule_label="Exit target: Bid ≥ ₹1,010"), bond, domain="x", tick_at=at)
    assert title == "DEV ACCELERATOR LIMITED — bid ₹1,015 (8.90%)"
    assert "Rule: Exit target: Bid ≥ ₹1,010" in body.split("\n")


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


def test_rule_label_is_fixed_order_field_op_value():
    assert opp.rule_label({"min_ask_ytm": 15, "filters": {"min_rating": "bbb-", "min_years": 0.5}}) \
        == "Ask YTM ≥ 15%, Rating ≥ BBB-, Maturity ≥ 6mo"
    assert opp.rule_label({"min_ask_ytm": 12.5, "min_ticket": 200_000,
                           "filters": {"min_years": 1, "max_years": 3, "payout": "bullet",
                                       "exchange": "NSE", "secured_only": True}}) \
        == ("Ask YTM ≥ 12.5%, Ticket ≥ ₹2 L, "
            "Maturity 1y–3y, Payout: bullet, Exchange: NSE, Secured")
    assert opp.rule_label({"min_ask_ytm": 9, "filters": {"max_years": 2.5, "sector": "NBFC"}}) \
        == "Ask YTM ≥ 9%, Maturity ≤ 2.5y, Sector: NBFC"
    assert opp.rule_label({"min_ticket": 25_000_000, "filters": {}}) == "Ticket ≥ ₹2.5 Cr"
    assert opp.rule_label({"filters": {}}) == "Any ask"
    assert opp.target_label({"min_bid_price": 1010, "max_bid_ytm": 9}) == "Exit target: Bid ≥ ₹1,010, Bid YTM ≤ 9%"


def test_thresholds_carry_the_rule_yield_for_the_recheck():
    th, _ = opp.build_thresholds([RULE], {7: ["A"]}, [], {("A", 12.0): 950.0})
    assert th.asks["A"][0].min_ask_ytm == 12.0
    assert th.asks["A"][0].label == "Ask YTM ≥ 12%, Ticket ≥ ₹50,000, Rating ≥ BBB-"
    targets = [{"isin": "H", "enabled": True, "min_bid_price": None, "max_bid_ytm": 9.0}]
    th, _ = opp.build_thresholds([], {}, targets, {("H", 9.0): 1030.0})
    assert th.bids["H"].max_bid_ytm == 9.0 and th.bids["H"].label == "Exit target: Bid YTM ≤ 9%"


# --- auto-buy ---------------------------------------------------------------------

NOW = datetime(2026, 10, 14, 5, 0, tzinfo=UTC)   # 10:30 IST


def armed_rule(**over) -> opp.AskRule:
    base = {"rule_id": 7, "label": "Ask YTM ≥ 12%", "max_ask_price": 950.0,
            "min_ticket": None, "min_ask_ytm": 12.0, "buy_budget": 10_000.0,
            "buy_daily_cap": 50_000.0, "auto_buy_until": NOW + timedelta(hours=5)}
    return opp.AskRule(**{**base, **over})


def test_size_order_is_budget_bound_and_ask_bound():
    assert opp.size_order(10_000, 997.0, 50) == 10
    assert opp.size_order(10_000, 997.0, 4) == 4
    assert opp.size_order(500, 997.0, 4) == 0
    assert opp.size_order(None, 997.0, 4) == 0
    assert opp.size_order(1e6, 99_218.42, 1) == 1


def gate(rule: opp.AskRule, h: opp.Hit, *, spent_rule: float = 0.0, spent_all: float = 0.0,
         global_cap: float | None = None, cash: float | None = None,
         already_ordered: bool = False, qty: int = 10) -> str | None:
    return opp.auto_buy_gate(rule, h, now=NOW, spent_rule=spent_rule, spent_all=spent_all,
                             global_cap=global_cap, cash=cash, already_ordered=already_ordered,
                             qty=qty)


def test_auto_buy_gate_reports_the_first_blocker():
    h = hit(price=997.0, qty=10, ytm=12.4, ytm_bound=12.0)
    assert gate(armed_rule(), h) is None
    assert gate(armed_rule(auto_buy_until=None), h) == "not armed"
    assert gate(armed_rule(auto_buy_until=NOW - timedelta(minutes=1)), h) == "not armed"
    assert gate(armed_rule(buy_daily_cap=None), h) == "no budget or daily cap set"
    assert gate(armed_rule(), hit(price=997.0, qty=10, ytm=11.6, ytm_bound=12.0)) \
        == "exact yield 11.60% is short of the rule"
    assert gate(armed_rule(), h, qty=0) == "budget ₹10,000 buys 0 units at ₹997.00"
    assert gate(armed_rule(), h, already_ordered=True) == "already ordered this bond today"
    assert gate(armed_rule(), h, spent_rule=45_000.0) == "rule daily cap reached (₹45,000 of ₹50,000)"
    assert gate(armed_rule(), h, global_cap=1e5, spent_all=95_000.0) \
        == "global daily cap reached (₹95,000 of ₹1 L)"
    assert gate(armed_rule(), h, cash=5_000.0) == "available cash ₹5,000 < order ₹9,970"


def test_render_order_texts():
    h = hit(price=997.0, qty=10, exchange="BSE", rule_label="Ask YTM ≥ 12%")
    bond = {"issuer_name": "ICL FINCORP LIMITED"}
    at = datetime(2026, 10, 14, 8, 4, 13, tzinfo=UTC)   # 13:34:13 IST
    title, body = opp.render_order("placed", 10, h, bond, order_id="2510140001", at=at)
    assert title == "Order placed: 10 × ICL FINCORP LIMITED — ₹9,970"
    assert body.split("\n") == ["LIMIT at ₹997 on BSE, Kite order 2510140001",
                                "Rule: Ask YTM ≥ 12%", "13:34:13 IST"]
    title, body = opp.render_order("rejected", 10, h, bond, reason="Insufficient funds", at=at)
    assert title == "Auto-buy rejected — ICL FINCORP LIMITED"
    assert body.split("\n")[:2] == ["Kite: Insufficient funds", "Tried 10 × ₹997 = ₹9,970 on BSE"]
    title, body = opp.render_order("skipped", 0, h, bond, reason="rule daily cap reached", at=at)
    assert title == "Auto-buy skipped — ICL FINCORP LIMITED"
    assert body.split("\n") == ["rule daily cap reached", "Rule: Ask YTM ≥ 12%", "13:34:13 IST"]
    _, body = opp.render_order("skipped", 10, h, bond, reason="available cash ₹500 < order ₹9,970", at=at)
    assert "Would have been 10 × ₹997 = ₹9,970 on BSE" in body.split("\n")
