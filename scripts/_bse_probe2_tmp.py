"""Temp probe: scrape BSE debt pages for their real API endpoints."""
import re

import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "*/*",
    "Referer": "https://www.bseindia.com/",
}

PAGES = [
    "https://www.bseindia.com/markets/debt/debt_corporate.aspx",
    "https://www.bseindia.com/markets/debt/TradenSettle.aspx",
    "https://www.bseindia.com/markets/debt/corporate_debt.aspx",
]

for page in PAGES:
    try:
        r = requests.get(page, headers=HEADERS, timeout=(5, 20))
        hits = set(re.findall(r"BseIndiaAPI/api/[A-Za-z_/]+", r.text))
        scripts = re.findall(r'src=["\x27]?([^"\x27 >]+\.js)', r.text)[:12]
        print(f"== {r.status_code} {page}", flush=True)
        for h in sorted(hits):
            print("   API:", h, flush=True)
        for s in scripts:
            print("   JS:", s, flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"ERR {page}: {exc}", flush=True)
