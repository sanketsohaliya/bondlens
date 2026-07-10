#!/bin/bash
# Temporary: end-to-end smoke test of the app on PostgreSQL.
set -e
B=http://localhost:8000
py=.venv/bin/python

echo "--- bonds list + rating override survived migration"
curl -s "$B/api/bonds" | $py -c '
import json, sys
d = json.load(sys.stdin)
bonds = d["bonds"]
print("bonds:", len(bonds), "fetched_at:", d.get("fetched_at"))
b = next(x for x in bonds if x["isin"] == "INE244L07168")
print("96SFIL24A:", b["credit_rating"], "|", b["rating_source"], "| rank", b["rating_rank"])
nsdl = sum(1 for x in bonds if x.get("rating_source") == "NSDL")
print("rows with NSDL-verified ratings:", nsdl)
'
echo "--- detail endpoint"
curl -s "$B/api/bonds/INE244L07168/detail" | $py -c '
import json, sys
d = json.load(sys.stdin)
print("exact_ytm:", d["exact_ytm"], "| flows:", len(d["cashflows"]), "| rating_url:", (d.get("rating_url") or "")[:60])
'
echo "--- watchlist round-trip"
curl -s -X POST "$B/api/watchlist/INE244L07168" > /dev/null
curl -s "$B/api/bonds?watchlist_only=true" | $py -c 'import json,sys; print("watchlist rows:", len(json.load(sys.stdin)["bonds"]))'
curl -s -X DELETE "$B/api/watchlist/INE244L07168" > /dev/null
echo "--- portfolio round-trip"
ID=$(curl -s -X POST "$B/api/portfolio/holdings" -H 'Content-Type: application/json' \
  -d '{"isin":"INE244L07168","qty":10,"buy_price":980,"buy_date":"2025-06-01"}' | $py -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "holding id: $ID"
curl -s "$B/api/portfolio" | $py -c '
import json, sys
d = json.load(sys.stdin)
h = d["holdings"][0]
print("P&L:", {k: h[k] for k in ("invested", "market_value", "realized", "total_pnl", "xirr")})
'
curl -s -X DELETE "$B/api/portfolio/holdings/$ID" > /dev/null && echo "cleaned up"
echo "--- price history (from migrated snapshots)"
curl -s "$B/api/bonds/INE244L07168/history" | $py -c 'import json,sys; print("history points:", len(json.load(sys.stdin)["history"]))'
