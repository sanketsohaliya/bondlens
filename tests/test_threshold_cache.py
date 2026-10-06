"""Threshold cache orchestration with the DB and the pool stubbed out."""
from concurrent.futures import Future
from datetime import date

from backend import opportunities
from backend import threshold_cache as tc
from backend.analytics import valuation

TODAY = date(2026, 10, 5)
RULE = {"id": 1, "name": "r", "enabled": True, "min_ask_ytm": 12.0}


class InlineExecutor:
    """ProcessPoolExecutor stand-in that runs submits synchronously."""

    def __init__(self, max_workers=None):
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, *args):
        self.calls += 1
        fut: Future = Future()
        fut.set_result(fn(*args))
        return fut


def test_needed_collects_every_yield_level_per_bond():
    rules = [RULE, {"id": 2, "name": "q", "enabled": True, "min_ask_ytm": 9.5},
             {"id": 3, "name": "off", "enabled": False, "min_ask_ytm": 7.0},
             {"id": 4, "name": "price only", "enabled": True, "min_ask_ytm": None}]
    targets = [{"isin": "H", "enabled": True, "max_bid_ytm": 8.0},
               {"isin": "G", "enabled": True, "max_bid_ytm": None}]
    pairs = tc.needed(rules, {1: ["A", "B"], 2: ["B"], 3: ["Z"], 4: ["A"]}, targets)
    assert pairs == {("A", 12.0), ("B", 12.0), ("B", 9.5), ("H", 8.0)}


def test_ensure_reuses_valid_rows_and_solves_only_the_rest(monkeypatch):
    stored: list = []
    monkeypatch.setattr(tc, "fingerprints", lambda isins: {i: f"fp-{i}" for i in isins})
    monkeypatch.setattr(tc.queries, "terms_for", lambda isins: {i: {"face_value": 1000} for i in isins})
    monkeypatch.setattr(tc.queries, "cashflows_for", lambda isins: {i: [] for i in isins})
    monkeypatch.setattr(tc, "load", lambda today, current: {("A", 12.0): 950.0})
    monkeypatch.setattr(tc, "store", lambda rows, today, current: stored.extend(rows))
    monkeypatch.setattr(tc, "_solve_bond",
                        lambda isin, ytms, terms, flows, today: [(isin, y, 900.0) for y in ytms])
    pool = InlineExecutor()
    prices, solved = tc.ensure([RULE], {1: ["A", "B", "C"]}, [], TODAY,
                               executor_cls=lambda max_workers: pool)
    assert solved == 2 and pool.calls == 2          # B and C only
    assert prices == {("A", 12.0): 950.0, ("B", 12.0): 900.0, ("C", 12.0): 900.0}
    assert sorted(stored) == [("B", 12.0, 900.0), ("C", 12.0, 900.0)]


def test_ensure_with_nothing_needed_touches_nothing(monkeypatch):
    monkeypatch.setattr(tc, "fingerprints", lambda isins: (_ for _ in ()).throw(AssertionError))
    assert tc.ensure([], {}, [], TODAY) == ({}, 0)


def test_load_drops_rows_whose_inputs_or_calculator_changed(monkeypatch):
    rows = [
        {"isin": "A", "ytm": 12, "price": 950, "schedule_version": "fp-A", "calculation_version": valuation.VERSION},
        {"isin": "B", "ytm": 12, "price": 940, "schedule_version": "old", "calculation_version": valuation.VERSION},
        {"isin": "C", "ytm": 12, "price": None, "schedule_version": "fp-C", "calculation_version": "ancient"},
        {"isin": "D", "ytm": 12, "price": None, "schedule_version": "fp-D", "calculation_version": valuation.VERSION},
    ]

    class Conn:
        def execute(self, *a):
            return self

        def fetchall(self):
            return rows

    class Ctx:
        def __enter__(self):
            return Conn()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(tc.db, "_conn", lambda: Ctx())
    out = tc.load(TODAY, {"A": "fp-A", "B": "fp-B", "C": "fp-C", "D": "fp-D"})
    assert out == {("A", 12.0): 950.0, ("D", 12.0): None}


def test_solve_bond_builds_once_and_prices_each_yield(monkeypatch):
    built = []
    monkeypatch.setattr(tc.schedule, "build",
                        lambda isin, flows, terms, today: built.append(isin) or object())
    monkeypatch.setattr(opportunities, "price_for_yield",
                        lambda sched, terms, ytm, today: 1000 - ytm * 10)
    out = tc._solve_bond("A", [8.0, 12.0], {}, [], TODAY)
    assert built == ["A"] and out == [("A", 8.0, 920.0), ("A", 12.0, 880.0)]
