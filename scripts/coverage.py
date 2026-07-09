"""How much of NSE's listed debt universe appears in our live feed?"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bondlens import db

names = db.company_names()
snapshot = db.latest_snapshot()
feed_symbols = {r["symbol"] for r in snapshot if r["symbol"]}
print(f"Listed debt symbols (NSE master): {len(names)}")
print(f"Symbols in today's traded feed:   {len(feed_symbols)}")
print(f"Listed but not in feed:           {len(set(names) - feed_symbols)}")
issuers = {n for n in names.values()}
feed_issuers = {names[s] for s in feed_symbols if s in names}
print(f"Issuers listed: {len(issuers)}, issuers in feed: {len(feed_issuers)}")
