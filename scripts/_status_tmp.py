import sys
sys.path.insert(0, ".")
from bondlens import db

with db._conn() as conn:
    total = conn.execute("SELECT COUNT(*) AS c FROM nsdl_meta").fetchone()["c"]
    with_alloc = conn.execute(
        "SELECT COUNT(*) AS c FROM nsdl_meta WHERE allotment_date IS NOT NULL"
    ).fetchone()["c"]
    bse = conn.execute(
        "SELECT COUNT(DISTINCT isin) AS n, MAX(trade_date) AS d FROM bse_quotes"
    ).fetchone()
print(f"nsdl_meta rows: {total}", flush=True)
print(f"with allotment_date: {with_alloc}", flush=True)
print(f"bse_quotes distinct ISINs: {bse['n']}, latest trade_date: {bse['d']}", flush=True)
