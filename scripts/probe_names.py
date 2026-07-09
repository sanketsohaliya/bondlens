"""Hunt for an NSDL public endpoint that carries the issuer/company name."""
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bondlens.nse import BASE_URL, HEADERS

session = requests.Session()
session.headers.update(HEADERS)
session.get(BASE_URL, timeout=15)

URLS = [
    # NSE archives: securities-available-for-trading masters
    "https://nsearchives.nseindia.com/content/equities/DEBT.csv",
    "https://nsearchives.nseindia.com/content/equities/DEBT_L.csv",
    "https://nsearchives.nseindia.com/content/equities/debt_l.csv",
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    # NSDL siblings of coupondetail
    "https://www.indiabondinfo.nsdl.com/bds-service/v1/public/bdsinfo/securitydesc?isin=INE916Y07040",
    "https://www.indiabondinfo.nsdl.com/bds-service/v1/public/bdsinfo/bonddirectory?isin=INE916Y07040",
]

for url in URLS:
    try:
        r = session.get(url, timeout=15)
        head = r.text[:250].replace("\n", " | ")
        print(f"{r.status_code}  {len(r.text):>9}B  {url}\n    {head}\n")
    except Exception as exc:  # noqa: BLE001
        print(f"ERR  {url}: {exc}")
