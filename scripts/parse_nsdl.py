#!/usr/bin/env python
"""Rebuild the parsed layer from nsdl_raw.

Every parsed table is a disposable derivation: this script TRUNCATEs and
re-parses the latest raw row per (isin, endpoint, quarter) inside one
transaction, so a parser fix + rerun heals the whole layer. Runs chained
after the harvester in cron (same flock), or standalone:

    .venv/bin/python scripts/parse_nsdl.py
"""
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bondlens import db                      # noqa: E402
from bondlens.nsdl_parse import PARSERS, parse_holdings   # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS bond_info (
    isin TEXT PRIMARY KEY REFERENCES bonds (isin),
    issuer_name TEXT, cin TEXT, lei TEXT,
    macro TEXT, sector TEXT, industry TEXT, basic_industry TEXT,
    sec_type TEXT, sec_type_desc TEXT, instrument_type TEXT,
    issuer_type_owner TEXT, issuer_type_nature TEXT,
    former_name_one TEXT, former_name_two TEXT, former_name_three TEXT,
    restructured_status TEXT, corporate_action_event TEXT, new_isin TEXT
);
CREATE TABLE IF NOT EXISTS bond_terms (
    isin TEXT PRIMARY KEY REFERENCES bonds (isin),
    series TEXT, category TEXT, secured TEXT, secured_details TEXT,
    tax_free TEXT, perpetual BOOLEAN,
    face_value NUMERIC, issue_price NUMERIC, total_issue_size NUMERIC,
    amount_raised NUMERIC, allotment_date DATE, redemption_date DATE,
    tenure_years INTEGER, tenure_months INTEGER, tenure_days INTEGER,
    mode_of_issue TEXT, instrument_desc TEXT, nature_of_instrument TEXT,
    green_shoe TEXT, asset_coverage TEXT, asset_cover_percent NUMERIC,
    coupon_rate NUMERIC, coupon_type TEXT, coupon_basis TEXT,
    interest_payment_frequency TEXT, frequency_description TEXT,
    day_count_convention TEXT, business_day_convention TEXT,
    coupon_payout_formula TEXT, coupon_reset_process TEXT,
    redemption_type TEXT, maturity_type TEXT, net_quantity NUMERIC,
    defaulted_redemption TEXT, early_redemption_details TEXT,
    redemption_premium_details TEXT,
    put_indicator TEXT, put_specified_dates TEXT, put_deadline TEXT,
    call_indicator TEXT, call_specified_dates TEXT, call_deadline TEXT
);
CREATE TABLE IF NOT EXISTS bond_cashflows (
    id BIGSERIAL PRIMARY KEY,
    isin TEXT NOT NULL REFERENCES bonds (isin),
    event TEXT, due_date DATE, record_date DATE, payment_date DATE,
    amount_payable NUMERIC, amount_redeem NUMERIC,
    amount_outstanding NUMERIC, actual_record_date DATE,
    actual_payment_date DATE, reason_delay_payment TEXT
);
CREATE INDEX IF NOT EXISTS idx_bond_cashflows_isin
    ON bond_cashflows (isin, due_date);
CREATE TABLE IF NOT EXISTS bond_further_issues (
    id BIGSERIAL PRIMARY KEY,
    isin TEXT NOT NULL REFERENCES bonds (isin),
    allotment_date DATE, allotment_quantity NUMERIC, issue_price NUMERIC,
    issue_size NUMERIC, cumulative_quantity NUMERIC,
    cumulative_issue_size NUMERIC
);
CREATE TABLE IF NOT EXISTS bond_redemption_events (
    id BIGSERIAL PRIMARY KEY,
    isin TEXT NOT NULL REFERENCES bonds (isin),
    redemption_date DATE, value_redeemed NUMERIC,
    quantity_redeemed NUMERIC, method TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS bond_ratings (
    id BIGSERIAL PRIMARY KEY,
    isin TEXT NOT NULL REFERENCES bonds (isin),
    agency TEXT, agency_name TEXT, rating TEXT, outlook TEXT,
    rating_action TEXT, rating_date DATE, rating_change_date DATE,
    watch TEXT, verification_status TEXT, press_release_link TEXT,
    is_current BOOLEAN NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bond_ratings_isin
    ON bond_ratings (isin, is_current, rating_date);
CREATE TABLE IF NOT EXISTS bond_listings (
    id BIGSERIAL PRIMARY KEY,
    isin TEXT NOT NULL REFERENCES bonds (isin),
    exchange TEXT, issue_type TEXT, listing_date DATE, allotment_date DATE,
    quantity NUMERIC, term_sheet_link TEXT, notification_link TEXT,
    is_current BOOLEAN NOT NULL
);
CREATE TABLE IF NOT EXISTS bond_documents (
    id BIGSERIAL PRIMARY KEY,
    isin TEXT NOT NULL REFERENCES bonds (isin),
    doc_name TEXT, source TEXT, download_link TEXT, file_id TEXT
);
CREATE TABLE IF NOT EXISTS bond_contacts (
    isin TEXT PRIMARY KEY REFERENCES bonds (isin),
    registrar TEXT, registrar_contact TEXT, registrar_contact_person TEXT,
    registrar_email TEXT, registrar_address TEXT, registrar_website TEXT,
    trustee_name TEXT, trustee_contact TEXT, trustee_email TEXT,
    trustee_address TEXT, trustee_website TEXT,
    lead_manager TEXT, lead_contact TEXT, lead_email TEXT,
    lead_address TEXT, lead_website TEXT,
    issuer_reg_address TEXT, issuer_contact_name TEXT,
    issuer_contact_email TEXT
);
CREATE TABLE IF NOT EXISTS bond_arrangers (
    id BIGSERIAL PRIMARY KEY,
    isin TEXT NOT NULL REFERENCES bonds (isin),
    arranger TEXT, contact_person TEXT, contact TEXT, email TEXT,
    website TEXT, address TEXT
);
CREATE TABLE IF NOT EXISTS bond_defaults (
    id BIGSERIAL PRIMARY KEY,
    isin TEXT NOT NULL REFERENCES bonds (isin),
    sr_no INTEGER, issue_nature TEXT, issue_size NUMERIC,
    interest_due_date DATE, redemption_due_date DATE,
    default_details TEXT, actual_payment_details TEXT,
    verification_date DATE, verification_status TEXT
);
CREATE TABLE IF NOT EXISTS bond_technical_defaults (
    id BIGSERIAL PRIMARY KEY,
    isin TEXT NOT NULL REFERENCES bonds (isin),
    agency TEXT, payment_due_date DATE, amount_to_be_paid NUMERIC,
    amount_paid NUMERIC, amount_payment_failed NUMERIC,
    failure_reason TEXT, update_date DATE, press_release_link TEXT
);
CREATE TABLE IF NOT EXISTS bond_restructurings (
    isin TEXT PRIMARY KEY REFERENCES bonds (isin),
    event TEXT, parent_isin TEXT, sec_type TEXT, category TEXT,
    coupon_rate TEXT, coupon_basis TEXT,
    allotment_date DATE, redemption_date DATE,
    interest_payment_frequency TEXT,
    parent_sec_type TEXT, parent_category TEXT, parent_coupon_rate TEXT,
    parent_coupon_basis TEXT, parent_allotment_date DATE,
    parent_redemption_date DATE, parent_interest_payment_frequency TEXT
);
CREATE TABLE IF NOT EXISTS bond_holdings (
    id BIGSERIAL PRIMARY KEY,
    isin TEXT NOT NULL REFERENCES bonds (isin),
    quarter TEXT NOT NULL, report_date DATE,
    header TEXT, subheader TEXT, holder_count INTEGER, quantity NUMERIC
);
CREATE INDEX IF NOT EXISTS idx_bond_holdings_isin
    ON bond_holdings (isin, quarter);
"""

TABLES = ["bond_info", "bond_terms", "bond_cashflows", "bond_further_issues",
          "bond_redemption_events", "bond_ratings", "bond_listings",
          "bond_documents", "bond_contacts", "bond_arrangers",
          "bond_defaults", "bond_technical_defaults", "bond_restructurings",
          "bond_holdings"]

LATEST = ("SELECT DISTINCT ON (isin, quarter) isin, quarter, payload "
          "FROM nsdl_raw WHERE endpoint = %s AND http_status = 200 "
          "ORDER BY isin, quarter, fetched_at DESC")

BATCH = 2000


def flush(conn, table, rows, counts):
    if not rows:
        return
    cols = sorted({c for r in rows for c in r})
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) "
           f"VALUES ({', '.join(['%s'] * len(cols))})")
    conn.cursor().executemany(sql, [[r.get(c) for c in cols] for r in rows])
    counts[table] += len(rows)
    rows.clear()


def main():
    t0 = time.time()
    counts = defaultdict(int)
    with db._conn() as conn:
        conn.execute(SCHEMA)
        conn.execute("TRUNCATE " + ", ".join(TABLES))

        terms = {}  # isin -> merged row from instruments+coupon+redemptions
        pending = defaultdict(list)

        for endpoint, parser in PARSERS.items():
            with conn.cursor(name=f"parse_ss_{endpoint}") as cur:
                cur.execute(LATEST, (endpoint,))
                for row in cur:
                    isin, payload = row["isin"], row["payload"]
                    try:
                        out = parser(isin, payload)
                    except Exception as exc:  # noqa: BLE001
                        print(f"{isin}/{endpoint}: PARSE ERROR "
                              f"{str(exc)[:80]}", flush=True)
                        counts["errors"] += 1
                        continue
                    for table, rows in out.items():
                        if table == "_terms":
                            for r in rows:
                                terms.setdefault(r["isin"],
                                                 {"isin": r["isin"]}).update(
                                    {k: v for k, v in r.items() if k != "isin"})
                        else:
                            pending[table].extend(rows)
                            if len(pending[table]) >= BATCH:
                                flush(conn, table, pending[table], counts)

        # holdings: quarter is part of the key, parser signature differs
        with conn.cursor(name="parse_ss_h") as cur:
            cur.execute(LATEST, ("publicshareholderdetails",))
            for row in cur:
                isin, quarter, payload = (row["isin"], row["quarter"],
                                          row["payload"])
                try:
                    out = parse_holdings(isin, quarter, payload)
                except Exception as exc:  # noqa: BLE001
                    print(f"{isin}/holdings/{quarter}: PARSE ERROR "
                          f"{str(exc)[:80]}", flush=True)
                    counts["errors"] += 1
                    continue
                pending["bond_holdings"].extend(out["bond_holdings"])
                if len(pending["bond_holdings"]) >= BATCH:
                    flush(conn, "bond_holdings", pending["bond_holdings"],
                          counts)

        for table, rows in pending.items():
            flush(conn, table, rows, counts)
        flush(conn, "bond_terms", list(terms.values()), counts)

    for table in TABLES:
        print(f"{table:26s} {counts[table]:>8}", flush=True)
    if counts["errors"]:
        print(f"{'PARSE ERRORS':26s} {counts['errors']:>8}", flush=True)
    print(f"DONE in {time.time() - t0:.0f}s", flush=True)
    db.close_pool()
    return 1 if counts["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
