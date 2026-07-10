import sys
sys.path.insert(0, ".")
from bondlens import bse, db

trade_date, quotes = bse.fetch_bhavcopy()
print(f"bhavcopy date: {trade_date}, rows: {len(quotes)}", flush=True)
universe = {r["isin"] for r in db.latest_snapshot() if r.get("isin")}
universe |= {h["isin"] for h in db.holdings()}
matched = [q for q in quotes if q["isin"] in universe]
print(f"universe: {len(universe)}, matched: {len(matched)}", flush=True)
if matched:
    db.save_bse_quotes(matched)
    print("saved", flush=True)
