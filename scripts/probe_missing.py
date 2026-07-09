"""Which IIFL Home Finance series are listed but absent from our feed,
and does NSE's liveBonds API have other `type=` buckets that carry them?"""
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bondlens import db
from bondlens.nse import BASE_URL, HEADERS

names = db.company_names()
snapshot = {r["symbol"] for r in db.latest_snapshot()}
iifl = sorted(s for s, n in names.items() if "IIFL HOME" in n.upper())
print(f"IIFL Home Finance listed series: {len(iifl)}")
print("  in feed:    ", sorted(s for s in iifl if s in snapshot))
print("  NOT in feed:", sorted(s for s in iifl if s not in snapshot))

session = requests.Session()
session.headers.update(HEADERS)
session.get(BASE_URL, timeout=15)
for typ in ["bonds", "gsec", "tb", "corpbond", "debt", "ncd", "sgb", "irfc"]:
    try:
        r = session.get(f"{BASE_URL}/api/liveBonds-traded-on-cm?type={typ}",
                        timeout=15)
        if r.ok and r.text.strip().startswith("{"):
            d = r.json()
            rows = d.get("data", [])
            hit = [x["symbol"] for x in rows
                   if x.get("symbol") in set(iifl) - snapshot]
            print(f"type={typ:<9} rows={len(rows):>5}  missing-IIFL found: {hit}")
        else:
            print(f"type={typ:<9} HTTP {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        print(f"type={typ:<9} ERR {exc}")
