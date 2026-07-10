"""Temp probe: find BSE endpoints serving traded corporate-bond data."""
import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
}

CANDIDATES = [
    # Guessed BseIndiaAPI paths for the debt segment
    "https://api.bseindia.com/BseIndiaAPI/api/DebtHighlights/w",
    "https://api.bseindia.com/BseIndiaAPI/api/DebtOTCTradeReport/w",
    "https://api.bseindia.com/BseIndiaAPI/api/CorpBondData/w",
    "https://api.bseindia.com/BseIndiaAPI/api/RetailDebtData/w",
    ("https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
     "?Group=&Scripcode=&industry=&segment=Debt+or+Others&status=Active"),
    # Market watch style
    "https://api.bseindia.com/BseIndiaAPI/api/MktWatch/w?flag=DB",
]

for url in CANDIDATES:
    try:
        r = requests.get(url, headers=HEADERS, timeout=(5, 15))
        body = r.text[:250].replace("\n", " ")
        print(f"{r.status_code} {url[:95]}\n   {body}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"ERR {url[:95]}: {exc}", flush=True)
