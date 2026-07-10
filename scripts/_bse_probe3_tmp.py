"""Temp probe: mine BSE's Angular bundle for debt-related API endpoints."""
import re

import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "*/*",
    "Referer": "https://www.bseindia.com/",
}

url = "https://www.bseindia.com/assets/includenew/js/main-Z3JUZS36.js"
js = requests.get(url, headers=HEADERS, timeout=(10, 60)).text
print("bundle bytes:", len(js), flush=True)
hits = set(re.findall(r"BseIndiaAPI/api/[^\"'`]{1,90}", js))
print("BseIndiaAPI refs:", len(hits), flush=True)
for h in sorted(hits)[:40]:
    print("  ", h, flush=True)
# Lazy-loaded chunks: find ones whose name hints at debt pages
chunks = set(re.findall(r"chunk-[A-Z0-9]{8}\.js", js))
print("chunks:", len(chunks), flush=True)
# Search every chunk name string near 'debt' route definitions
for m in re.finditer(r"debt[^\"'`]{0,60}", js[:2_000_000], re.I):
    pass
routes = re.findall(r"path:\"([^\"]*debt[^\"]*)\"[^}]{0,120}?import\(\"\./(chunk-[A-Z0-9]{8}\.js)\"\)", js, re.I)
for r in routes[:20]:
    print("route:", r, flush=True)
