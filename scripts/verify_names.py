"""Verify company names are attached to bonds after refresh."""
import json
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

d = json.load(urlopen("http://localhost:8000/api/bonds"))
named = [b for b in d["bonds"] if b.get("company_name")]
print(f"{len(named)} of {d['count']} bonds have company names")
for b in named[:5]:
    print(f"  {b['symbol']:<12} {b['company_name']}")
missing = [b["symbol"] for b in d["bonds"] if not b.get("company_name")]
if missing:
    print("missing:", missing[:10])
