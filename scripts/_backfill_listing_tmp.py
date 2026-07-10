import sys
import time
sys.path.insert(0, ".")
from bondlens import db, nsdl

isins = db.isins_missing_listing()
print(f"missing listing: {len(isins)}", flush=True)
ok = err = 0
for i, isin in enumerate(isins, 1):
    try:
        db.save_nsdl_listing(isin, nsdl.fetch_listing(isin))
        ok += 1
    except Exception as exc:  # noqa: BLE001
        err += 1
    if i % 25 == 0:
        print(f"{i}/{len(isins)} ok={ok} err={err}", flush=True)
    time.sleep(0.3)
print(f"done: ok={ok} err={err}", flush=True)
