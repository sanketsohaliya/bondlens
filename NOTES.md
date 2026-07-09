# BondLens — Project Notes

Context handoff file: architecture, decisions, caveats, and pending work.
Written 2026-07-09 when the folder was renamed BondScanner → BondLens.

## What this is

Personal dashboard for screening NSE-listed corporate bonds. FastAPI +
SQLite backend (`bondlens/`), single-file frontend (`static/index.html`,
Tailwind CDN + vanilla JS). Runs locally in WSL (Ubuntu-20.04):

```
.venv/bin/uvicorn bondlens.app:app --host 0.0.0.0 --port 8000
```

`scripts/restart.sh` restarts it. Logs at `/tmp/bondlens.log`.

## Architecture

- `bondlens/nse.py` — NSE bond-market snapshot (prices, traded value, ratings)
- `bondlens/nsdl.py` — exact cash-flow schedules from NSDL India Bond Info
  (public JSON API), cached permanently in SQLite; plus schedule repair
  helpers (`fill_amounts`, `normalize_flows`, `redemption_matches_face`)
- `bondlens/ytm.py` — the math: `exact_ytm` (XIRR over dated flows),
  `ytm` (quick approximation), `modified_duration`, `accrued_interest`
- `bondlens/gsec.py` — G-Sec yield curve for spread calculation
- `bondlens/db.py` — SQLite storage: snapshots, NSDL cache, watchlist,
  rating-change history
- `bondlens/app.py` — API endpoints + screening filters/presets
- `tests/test_math.py` — unit tests for all the math (run:
  `python3 -m unittest discover tests`)

## Key decisions & bug history

- **Exact YTM is only shown when trustworthy.** Guards, in order:
  NSDL status must be "ok"; amounts blank → filled from coupon rate
  (`fill_amounts`, flagged `amounts_estimated`); issue-level amounts
  (~100×+ face) scaled down (`normalize_flows`, only scales ratios ≥100
  to avoid clobbering amortizers); **future principal must sum to NSE's
  face value ±2%** (`redemption_matches_face`) — else exact YTM and
  duration are withheld, UI shows amber "doesn't match face value".
- That last guard exists because of **79NHIT40** (NHIT, amortizing
  staggered-redemption NCD): NSE quotes residual face ₹300 but NSDL's
  schedule carried ₹600 of future principal → fictional 19.22% YTM
  (real ~8%). Amortizers are the recurring data-quality failure mode.
- **What-if pricing**: `GET /api/bonds/{isin}/detail?price=X` recomputes
  all price-derived figures (exact/approx YTM, clean price, duration,
  spread, invest projection). UI: "What-if price" input in the detail
  drawer; recomputed figures highlighted amber; real-price payload stays
  in `detailCache`, what-if lives in `whatifDetail` and resets on
  drawer close.
- Published/advertised returns vs investor returns, record-date logic:
  flows are `future` only if due date > today AND record date (if any)
  > today — a buyer today wouldn't receive a payout whose record date
  passed.

## Data caveats (permanent)

- NSDL schedules can be wrong for amortizing bonds (see above), blank
  (`amounts_estimated`), or absent (`nsdl_status` = "empty"/uncached).
- LTP can be stale for illiquid bonds (`days_since_traded` tracked;
  UI flags stale). Thin trades distort YTM — check traded value.
- Approx YTM (`ytm_method: "approx"`) assumes annual coupons — off by
  up to ~0.3% for monthly payers.

## Pending / planned work

1. **Bond P&L tracker — BUILT** (branch `feature/pnl-tracker`,
   2026-07-09). Manual holdings entry only:
   - `bondlens/pnl.py` (realized/unrealized/XIRR math),
     `xirr()` in `ytm.py`, `holdings` table in `db.py`,
     `/api/portfolio*` endpoints, "Portfolio" view in the UI,
     `tests/test_pnl.py`.
   - Per holding: realized income (coupons + principal instalments,
     record-date filtered), unrealized (mark to dirty LTP), XIRR over
     all dated flows; portfolio totals + combined XIRR
     (`xirr_complete` false unless every holding has buy date+price).
   - Caveats surfaced as per-holding `warnings`: schedule mismatch,
     stale LTP, estimated amounts, missing buy date, and **schedule
     coverage gaps** — NSDL only lists payments still outstanding at
     fetch time, so payouts due between the buy date and the earliest
     cached flow are invisible (warned when gap > 35 days). TDS not
     modelled; income is gross.
   - **Zerodha Kite Connect integration was built then removed on
     request** (user prefers manual entry). If ever revisited: holdings
     API gives qty + average_price but NO buy dates; daily token
     refresh; redirect URL must be registered. Zerodha Console
     tradebook CSV import remains a possible later addition.

## Operational notes (WSL quirks)

- The venv hardcodes absolute paths in `.venv/bin/*` shebangs and
  `pyvenv.cfg` — if the folder is ever moved/renamed again, sed-replace
  the old path or recreate the venv.
- Running commands from Windows via `wsl -d Ubuntu-20.04 -e bash -c`:
  terminal output rendering is unreliable — write command output to a
  file under `/tmp` and read the file instead. Beware PowerShell
  mangling `$?`, `||`, nested quotes.
- `pkill -f 'uvicorn bondlens'` can match its own wrapper shell — use
  `pkill -f 'uvicorn[ ]bondlens'`.
- Git identity is repo-local: `sanket <sanket@localhost>` — change
  `user.email` before pushing anywhere public.
