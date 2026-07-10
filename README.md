# BondLens

Personal analytics dashboard for screening corporate bonds traded on NSE.

## Features

- Fetches live bond data (price, coupon, rating, volume) from NSE's website API
- Computes YTM (annual-coupon approximation, actual/365)
- Stores daily snapshots in PostgreSQL (append-only, full history kept) for price history — set `DATABASE_URL`, default `postgresql://bondlens:bondlens@localhost:5432/bondlens`
- Web dashboard: filter by rating / YTM / maturity / traded value, sortable columns, watchlist

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn bondlens.app:app --reload
```

Open http://127.0.0.1:8000 and click **Refresh from NSE**.

## Exact YTM via NSDL

Click **Fetch NSDL data** to bulk-download exact cash-flow schedules from
NSDL India Bond Info (public API) for all ISINs in the latest snapshot that
aren't cached yet. Requests are spaced 10 seconds apart (a full first run of
~1,400 ISINs takes ~4 hours in the background; it is resumable and later runs
only fetch new ISINs).

Bonds with a cached schedule show an exact YTM (marked with a filled dot):
XIRR over the actual dated cash flows, record-date aware, solved against the
last traded price. Uncached bonds fall back to the annual-coupon
approximation (hollow dot, roughly ±0.5%).

## Notes / Caveats

- NSE endpoints are unofficial and may change or rate-limit; the fetcher does a
  cookie handshake with a browser User-Agent.
- Approximate YTM assumes annual coupons with estimated accrued interest.
- Exact YTM is exact for the last traded price; stale prices on illiquid bonds
  remain the main source of real-world deviation. Floating-rate bonds use
  provisional future amounts.
- `coupon` is null for zero-coupon/discount bonds; YTM still computes from
  price vs face value.
- Snapshots accumulate each time you refresh — history per ISIN is available at
  `/api/bonds/{isin}/history`.
- `scripts/seed_one.py <ISIN>` fetches a single NSDL schedule for testing.
