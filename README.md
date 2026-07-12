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
  - 05:30 Mon–Sat: universe build (`backend/universe.py`)
  - 07:00 daily: NSDL harvest + parse (`scripts/harvest_nsdl.py`, `parse_nsdl.py`)
  - 19:00 Mon–Fri (+07:15 retry): EOD trades (`scripts/harvest_eod.py`, `parse_eod.py`)
  - 08:00 daily: DB backup to the Windows Desktop (`scripts/backup_db.py`)
  - 08:45 daily + 19:45 Mon–Fri: healthcheck with ntfy alerts (`scripts/healthcheck.py`)

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
.venv/bin/python scripts/migrate.py        # apply pending migrations
.venv/bin/python -m pytest tests/ -q
.venv/bin/uvicorn backend.app:app --reload

# frontend (vite dev server proxies /api to :8000)
cd frontend && npm install && npm run dev
npm run codegen   # regenerate TS types from the API's OpenAPI schema
npm run check     # biome + tsc
npm test          # vitest

# schedule-engine coverage report
.venv/bin/python scripts/validate_schedules.py -v

# quality gates (also run as git pre-commit hooks)
.venv/bin/pre-commit run --all-files
```

## Architecture notes

- **Raw-first**: every fetched file/API response is archived verbatim
  (`raw_files`, `nsdl_raw`, `eod_raw`) before parsing, so parsers can be
  rewritten and re-run over history without refetching.
- **Parse layers are disposable**: `parse_nsdl.py` truncate-rebuilds the
  `bond_*` tables; `parse_eod.py` appends to `eod_trades` incrementally.
- **Analytics** (`backend/analytics/`): `schedule.py` normalizes NSDL
  cashflow schedules (staggered redemptions, zero-coupon accretion,
  issue-level rescaling, estimated amounts); `yields.py` does day-count-
  aware YTM/YTW/accrued/duration; `ratings.py` derives composite grades,
  change flags and default status from agency rating trails.
- **Precomputed screener**: `scripts/compute_analytics.py` (run by the
  jobs container after every parse) writes per-bond analytics into
  `bond_analytics`; the API serves the screener as a stateless indexed
  SELECT. Date-relative fields (staleness, years to maturity) are
  derived in SQL at read time.
- **Contract-first API**: responses are validated against Pydantic
  models (`backend/models.py`); the frontend's TypeScript types are
  generated from the OpenAPI schema (`npm run codegen`), so backend and
  frontend cannot drift silently.
- **Migrations**: schema lives in `migrations/*.sql`, applied in order
  by `scripts/migrate.py` (jobs container runs it on start; tracked in
  `schema_migrations`).
- **Prices**: retail trades are dirty ₹ per unit (headline LTP); OTC
  trades are clean % of face and shown/charted separately.
- **Quality gates**: ruff + basedpyright + pytest + Biome, enforced via
  pre-commit hooks.

## Operations

- **Alerts**: install the [ntfy](https://ntfy.sh) app and subscribe to the
  topic set as `NTFY_TOPIC` in `docker-compose.yml`. The healthcheck
  pushes only when something is wrong (job failed/missing, stale trades,
  stale analytics, stuck runs). Test the wiring:
  `docker compose exec jobs python scripts/healthcheck.py --ping`
- **Backups**: `C:\Users\sanket.patel\Desktop\bondlens.dump` is a full
  `pg_dump -Fc`, replaced atomically every morning. Restore with
  `pg_restore -d bondlens bondlens.dump`.
- **Container logs** are capped at 10MB × 3 files per service
  (`docker compose logs jobs` to view).
- **E2E pipeline check** (live scrape → parse → analytics against a
  throwaway DB): `.venv/bin/pytest -m e2e -v -s`
