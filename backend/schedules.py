"""Build a bond's schedule with the exchange-notice overlay applied.

schedule.build normalises NSDL's rows; exchange_evidence matches BSE/NSE
payment notices to them; where a notice states a different record date
the exchange wins (that is the register date the depository uses). Every
caller that prices or projects a bond goes through here so the drawer,
the explorer, the portfolio and the alert thresholds agree.
"""
from __future__ import annotations

from datetime import date

from . import exchange_evidence, queries
from .analytics import schedule


def build(isin: str, cashflows: list[dict], terms: dict | None, today: date,
          notice_rows: list[dict] | None = None) -> schedule.Schedule:
    sched = schedule.build(isin, cashflows, terms, today=today)
    if notice_rows:
        overrides = exchange_evidence.record_date_overrides(isin, sched.flows, notice_rows)
        if overrides:
            schedule.apply_record_dates(sched, overrides)
    return sched


def build_for(isin: str, today: date) -> schedule.Schedule:
    """Per-bond convenience: reads rows, terms and notices itself."""
    return build(isin, queries.bond_cashflows(isin), queries.bond_terms(isin), today,
                 queries.exchange_notice_rows([isin]).get(isin, []))
