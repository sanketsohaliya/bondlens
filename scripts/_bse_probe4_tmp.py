"""Temp probe: BSE bhavcopy files (equity CM incl. F-group bonds, debt)."""
import csv
import io

import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "*/*",
    "Referer": "https://www.bseindia.com/",
}

DAY = "20260709"  # Thursday
URLS = [
    f"https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{DAY}_F_0000.CSV",
    f"https://www.bseindia.com/download/BhavCopy/Debt/BhavCopy_BSE_DB_0_0_0_{DAY}_F_0000.CSV",
    f"https://www.bseindia.com/download/BhavCopy/Debt/DEBTBHAVCOPY{DAY[6:]}{DAY[4:6]}{DAY[2:4]}.zip",
]

for url in URLS:
    try:
        r = requests.get(url, headers=HEADERS, timeout=(10, 60))
        print(f"== {r.status_code} {len(r.content)}B {url[-60:]}", flush=True)
        if not r.ok or url.endswith(".zip"):
            continue
        rows = list(csv.DictReader(io.StringIO(r.text)))
        print("rows:", len(rows), "| cols:", list(rows[0].keys())[:14], flush=True)
        series = {}
        for row in rows:
            s = row.get("SctySrs") or row.get("series") or "?"
            series[s] = series.get(s, 0) + 1
        print("series counts:", dict(sorted(series.items(),
                                            key=lambda x: -x[1])[:12]), flush=True)
        bond = next((x for x in rows if (x.get("SctySrs") or "") == "F"), None)
        print("sample F row:", bond, flush=True)
    except Exception as exc:  # noqa: BLE001
        print("ERR", url[-60:], exc, flush=True)
