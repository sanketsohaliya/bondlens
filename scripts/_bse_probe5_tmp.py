"""Temp probe: overlap between BSE bhavcopy and BondLens NSE universe."""
import csv
import io

import requests

from bondlens import db

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "*/*",
    "Referer": "https://www.bseindia.com/",
}

url = ("https://www.bseindia.com/download/BhavCopy/Equity/"
       "BhavCopy_BSE_CM_0_0_0_20260710_F_0000.CSV")
r = requests.get(url, headers=HEADERS, timeout=(10, 60))
print("today status:", r.status_code, len(r.content), flush=True)
if not r.ok or r.text.startswith("<!"):
    url = url.replace("20260710", "20260709")
    r = requests.get(url, headers=HEADERS, timeout=(10, 60))
    print("fallback status:", r.status_code, flush=True)

rows = list(csv.DictReader(io.StringIO(r.text)))
universe = {x["isin"] for x in db.latest_snapshot() if x["isin"]}
bse_isins = {x["ISIN"] for x in rows}
overlap = universe & bse_isins
print("bse rows:", len(rows), "| nse universe:", len(universe),
      "| overlap:", len(overlap), flush=True)
sample = [x for x in rows if x["ISIN"] in overlap][:3]
for s in sample:
    print(s["ISIN"], s["TckrSymb"], s["SctySrs"], s["LastPric"],
          s["TtlTradgVol"], s["TtlTrfVal"], s["TradDt"], flush=True)
# Debt-looking ISINs on BSE that NSE feed doesn't have (potential expansion)
extra = [x for x in rows
         if x["ISIN"] not in universe and x["SctySrs"] in ("F", "FC", "G")
         and x["ISIN"].startswith("INE")]
print("BSE-only F/G rows:", len(extra), flush=True)
