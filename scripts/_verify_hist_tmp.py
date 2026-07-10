"""One-off: fetch + archive rating history for one ISIN, then read it back."""
from bondlens import db, nsdl

ISIN = "INE530B07310"
current, history = nsdl.fetch_rating_data(ISIN)
db.save_nsdl_ratings(ISIN, current)
db.save_nsdl_rating_history(ISIN, history)
db.save_nsdl_rating_history(ISIN, history)  # idempotency check: no dupes
rows = db.nsdl_rating_history(ISIN)
print("archived events:", len(rows))
for r in rows:
    print(r["changed_at"], r["agency"], r["rating"],
          r["outlook"] or "-", r["rating_action"] or "-",
          "pr" if r["press_release"] else "-")
