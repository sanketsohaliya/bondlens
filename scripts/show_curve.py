import json
import urllib.request

curve = json.load(urllib.request.urlopen(
    "http://localhost:8000/api/gsec"))["curve"]
for p in curve:
    print(f"{p['tenor']:>6}  {p['yield']:>6}  {p['symbol']:<12} "
          f"traded={p['traded_value']}")
