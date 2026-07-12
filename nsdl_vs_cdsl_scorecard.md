# NSDL vs CDSL — Head-to-Head Source Evaluation

Sample: 48 listed bonds (stratified: dual/NSE-only/BSE-only, suspended, ZCB,
perpetual, PSU, bank, InvIT, near-maturity, recent, long-dated, plus 6 bonds
missing from NSDL's bulk report). Raw data: `/tmp/nsdl_sample/`,
`/tmp/cdsl_probe_out/`, `/tmp/cdsl_existence.json`. Field-level inventory:
`/tmp/nsdl_analysis.txt`.

## API surface discovered

### NSDL (indiabondinfo.nsdl.com, `bds-service/v1`) — 13 per-ISIN endpoints
Extracted from their Angular bundle. Status across the 48-bond sample:

| Endpoint | Status | Content |
|---|---|---|
| `/public/isins?isin=` | 48/48 ok | Issuer master: name, former names, CIN, LEI, PSU/non-PSU, sector/industry/macro, related-issuer list |
| `/public/bdsinfo/coupondetail?isin=` | 48/48 ok | Coupon basis + full dated cashflow schedule (due/record/payment dates, per-unit amounts, event type) |
| `/public/bdsinfo/credit-ratings?isin=` | 48/48 ok | Current + earlier ratings: agency, rating, outlook, action, dates, press-release links, CRA-verified (up to 32 history rows) |
| `/public/bdsinfo/listings?isin=` | 46/48 ok | Allotment date + per-exchange listing dates |
| `/public/bdsinfo/redemptions?isin=` | 48/48 ok | Redemption schedule/terms |
| `/public/bdsinfo/instruments?isin=` | 48/48 ok | Deep instrument detail: face value, issue price/size, amount raised, secured flag + security details, seniority, convertibility, credit enhancement/guarantees, green shoe, perpetual flag, tenure, tax-free, further issues (re-openings), put/call, step-up |
| `/public/bdsinfo/keycontacts?isin=` | 48/48 ok | RTA, debenture trustee, compliance contacts |
| `/public/bdsinfo/keydocuments?isin=` | 48/48 ok | Offer docs/IM links (+ `downloadkeydocuments?fileid=`) |
| `/public/bdsinfo/getTDbyIsin?isin=` | 48/48 ok | Technical default flag (empty payload when clean) |
| `/public/bdsinfo/quarterenddate?isin=` | 48/48 ok | Latest disclosure quarter |
| `/public/bdsinfo/defaultdetail?isin=` | 48/48 HTTP 400 | Needs different params (default history) |
| `/public/bdsinfo/restructuring?isin=` | 48/48 HTTP 400 | Needs different params |
| `/public/bdsinfo/isindisplay?isin=` | 48/48 HTTP 503 | Disabled server-side |

Plus bulk: `advancedSearchReport` (full-universe Excel; ~17% gaps) and
`listofsecurities?type=` (bulk list download).

### CDSL (cdslindia.com) — 5 surfaces probed

| Surface | Result |
|---|---|
| `SearchISIN.aspx` postback (main bond detail) | **Blocked: reCAPTCHA** ("Please select captcha before proceeding") |
| `eservices/ISINSearchLiq` (liquidation info) | **Blocked: reCAPTCHA** (sitekey embedded in form) |
| `SearchISIN.aspx/ISIN` webmethod (typeahead) | Works, but returns **only the ISIN string** — existence check, zero detail |
| `CorporateBondReports.aspx` (CM + OTC trade reports, no captcha) | Backend **HTTP 200 error page**: "Internal Error Occurred Please try after some time" (every attempt; same behavior as last week's SearchISIN 500s) |
| `eservices/SdiNcrpsISINSearch` (no captcha) | Works, but **SDI/NCRPS instruments only** — 7 columns (ISIN, issuer, instrument, coupon, description, issue/maturity dates); returned nothing for regular corporate bonds |

## Scorecard

| Criterion | NSDL | CDSL | Winner |
|---|---|---|---|
| **Coverage (48-bond sample)** | 48/48 on per-ISIN endpoints — including all 6 bonds absent from its own bulk report | 42/48 known to its registry (missed IRFC, Axis Bank, IREDA, TANGEDCO, Muthoot Fincorp, Edelweiss ARC bonds); 0/48 with any detail | **NSDL** |
| **Richness (security master)** | ~60 fields incl. CIN, LEI, secured details, seniority, credit enhancement, further issues | 7 columns, SDI/NCRPS only | **NSDL** |
| **Cashflows** | Full dated schedule per ISIN, redemption + interest legs | None accessible | **NSDL** |
| **Ratings** | Current + full history (up to 32 rows), CRA-verified, press-release links | None accessible | **NSDL** |
| **Listing dates** | Per-exchange allotment/listing dates (46/48) | None accessible | **NSDL** |
| **Default/distress info** | Technical-default endpoint ok; defaultdetail needs param probing | "ISIN Default Status" radio exists but captcha-gated | **NSDL** |
| **Reliability & ergonomics** | Clean JSON, no auth, no captcha, ~0.7s/req sustained 624 requests with zero throttling | Captcha on both detail surfaces; report backend returning internal errors for days; JSON API is typeahead-only | **NSDL** |
| **Unique value** | Everything above | Only: SDI/NCRPS niche list; OTC trade reports *if* their backend ever recovers | **NSDL** |

## Verdict

- **Overall winner: NSDL — by a wide margin, in every family.**
- CDSL exposes **no automatable per-ISIN corporate bond detail**: the two
  detail pages require reCAPTCHA, the captcha-free report page is broken
  server-side, and the JSON webmethod only echoes the ISIN.
- CDSL is worth **one thing** going forward: a periodic existence sweep
  (typeahead webmethod) as a cross-check, and a retry of
  `CorporateBondReports.aspx` occasionally for OTC trade data. Not a
  reference-data source.
- Key NSDL nuance: the **bulk report understates coverage** — the per-ISIN
  endpoints served all 6 sample bonds that the advancedSearchReport misses.
  So the enrichment crawler should key off our own `bonds` universe (from
  NSE/BSE masters) and hit per-ISIN endpoints, not trust the bulk dump.

## Proposed enrichment schema (Phase 5 — for approval)

All raw responses archived in `raw_files` (source =
`nsdl_<endpoint>`, gzip + SHA256 dedupe, `run_id` FK), parsed into:

- **`bond_details`** (1:1 with bonds; from `isins` + `instruments`):
  issuer_name, cin, lei, issuer_type, sector/industry, instrument_desc,
  face_value, issue_price, issue_size, amount_raised, allotment_date,
  redemption_date, secured, seniority, perpetual, tax_free, coupon_type,
  coupon_rate, interest_frequency, mode_of_issue + fetched_at
- **`bond_cashflows`** (append-only; from `coupondetail`): isin, event_type
  (interest/redemption), due_date, record_date, payment_date, amount_per_unit
- **`bond_ratings`** (append-only; from `credit-ratings`): isin, agency,
  rating, outlook, action, rating_date, verified_date, press_release_url,
  is_current
- **`bond_listing_dates`** (from `listings`): isin, exchange, listing_date,
  allotment_date
- Refresh policy: details/listings once + on `bond_events` change;
  cashflows once (static) + re-fetch on restructuring signals; ratings
  weekly; new bonds fetched as they enter the universe.
