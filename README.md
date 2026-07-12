# BondLens

Personal data platform and screener for Indian listed corporate bonds.

Scheduled jobs track the full NSE+BSE corporate-bond universe, enrich it
with NSDL's issue data (terms, cashflow schedules, credit ratings,
defaults) and capture end-of-day trade data from all four exchange
streams (NSE/BSE × retail order book / OTC reported) back to 2010.
A FastAPI backend computes schedule-exact YTMs, accrued interest,
durations and rating signals on top; a React frontend serves the
screener.

## Stack

- **db** — PostgreSQL 18, single `bondlens` database
- **api** — Python 3.14, FastAPI, psycopg 3 (port 8000)
- **web** — React 19 + TypeScript, Vite 7, Tailwind 4, lightweight-charts,
  served by nginx which proxies `/api` (port 3000)
- **jobs** — supercronic running the scheduled harvesters (IST):
  - 05:30 Mon–Sat: universe build (`bondlens/universe.py`)
  - 07:00 daily: NSDL harvest + parse (`scripts/harvest_nsdl.py`, `parse_nsdl.py`)
  - 19:00 Mon–Fri (+07:15 retry): EOD trades (`scripts/harvest_eod.py`, `parse_eod.py`)

## Run

```sh
docker compose up -d --build
# app:  http://localhost:3000
# api:  http://localhost:8000/docs
```

## Development

```sh
# backend (uv-managed venv against the container DB)
uv sync
.venv/bin/python -m pytest tests/ -q
.venv/bin/uvicorn bondlens.app:app --reload

# frontend (vite dev server proxies /api to :8000)
cd frontend && npm install && npm run dev

# schedule-engine coverage report
.venv/bin/python scripts/validate_schedules.py -v
```

## Architecture notes

- **Raw-first**: every fetched file/API response is archived verbatim
  (`raw_files`, `nsdl_raw`, `eod_raw`) before parsing, so parsers can be
  rewritten and re-run over history without refetching.
- **Parse layers are disposable**: `parse_nsdl.py` truncate-rebuilds the
  `bond_*` tables; `parse_eod.py` appends to `eod_trades` incrementally.
- **Analytics** (`bondlens/analytics/`): `schedule.py` normalizes NSDL
  cashflow schedules (staggered redemptions, zero-coupon accretion,
  issue-level rescaling, estimated amounts); `yields.py` does day-count-
  aware YTM/YTW/accrued/duration; `ratings.py` derives composite grades,
  change flags and default status from agency rating trails.
- **Prices**: retail trades are dirty ₹ per unit (headline LTP); OTC
  trades are clean % of face and shown/charted separately.
