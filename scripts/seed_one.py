"""Seed a single ISIN's NSDL schedule for testing the exact-YTM path."""
import sys

sys.path.insert(0, ".")

from bondlens import db, nsdl  # noqa: E402

isin = sys.argv[1] if len(sys.argv) > 1 else "INE477L08154"
meta, flows = nsdl.fetch_schedule(isin)
db.save_nsdl(isin, "ok", meta, flows)
print(isin, meta["frequency"], meta["coupon_basis"], len(flows), "flows")
