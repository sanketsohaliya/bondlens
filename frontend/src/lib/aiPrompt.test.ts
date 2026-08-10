import { describe, expect, it } from "vitest";
import type { BondDetail, Cashflow, OrderBook, RatingEvent } from "../api/types";
import { buildBondPrompt } from "./aiPrompt";

/* Minimal-but-valid BondDetail; tests override what they exercise. */
const base = {
  isin: "INE000TEST01",
  status: "active",
  on_nse: false,
  on_bse: true,
  bse_status: null,
  issuer_name: "TEST FINCORP LIMITED",
  nse_symbol: null,
  nse_series: null,
  bse_scrip_code: null,
  bse_scrip_id: "TSTFL",
  sector: "Financial Services",
  industry: null,
  basic_industry: null,
  macro: null,
  cin: null,
  lei: null,
  first_seen: null,
  last_seen: null,
  terms: { secured: "Yes", tax_free: "No" },
  kite_url: null,
  ltp: 9800,
  custom_price: false,
  trade_date: "2026-07-17",
  exchange: "BSE",
  days_since_traded: 1,
  otc_price_pct: 99.49,
  otc_yield: 14.11,
  otc_trade_date: "2026-07-17",
  coupon: 13,
  implied_coupon: null,
  face_value: 10000,
  frequency: "monthly",
  schedule_shape: "staggered",
  schedule_usable: true,
  schedule_warnings: [],
  amounts_estimated: false,
  issue_date: "2023-12-19",
  maturity_date: "2028-12-19",
  years_to_maturity: 2.42,
  ytm_exact: 14.17,
  ytm_approx: 13.9,
  ytm: 14.17,
  yield_to_worst: null,
  call_dates: [],
  current_yield: 13.27,
  accrued: 103,
  clean_price: 9697,
  duration: 1.72,
  credit_rating: "ACUITE BB+",
  rating_rank: 11,
  in_default: false,
  tech_delay: false,
  rating_timeline: {
    ACUITE: [
      {
        rating: "BB+",
        outlook: "Stable",
        action: "Affirmed",
        rating_date: "2026-03-12",
        press_release: null,
      },
    ],
  },
  cashflows: [
    {
      event: "interest",
      due_date: "2026-08-19",
      record_date: null,
      amount: 110,
      estimated: false,
      future: true,
    },
    {
      event: "redemption",
      due_date: "2028-12-19",
      record_date: null,
      amount: 10000,
      estimated: false,
      future: true,
    },
  ],
  future_total_per_unit: 12560,
  next_payment: {
    event: "interest",
    due_date: "2026-08-19",
    record_date: null,
    amount: 110,
    estimated: false,
    future: true,
  },
  listings: [],
  documents: [],
  further_issues: [],
  redemption_events: [],
  defaults: [],
  technical_defaults: [],
  issuer_bonds: [],
} as unknown as BondDetail;

const mk = (over: Partial<BondDetail> = {}): BondDetail => ({ ...base, ...over });

describe("buildBondPrompt", () => {
  it("includes the key figures", () => {
    const p = buildBondPrompt(mk());
    expect(p).toContain("ISIN: INE000TEST01");
    expect(p).toContain("TEST FINCORP LIMITED");
    expect(p).toContain("Coupon: 13.00% monthly, face value ₹10,000");
    expect(p).toContain("Matures: 19 Dec 2028 (2y 5m)");
    expect(p).toContain("YTM: 14.17% (exact, from published cashflows)");
    expect(p).toContain("Duration: 1y 9m");
    expect(p).toContain("OTC institutional benchmark: 99.49% of face @ 14.11%");
    expect(p).toContain("Secured: yes | Tax-free: no");
    expect(p).toContain("ACUITE BB+ (Stable) Affirmed 12 Mar 2026");
    expect(p).toContain("19 Dec 2028  redemption  ₹10,000");
    expect(p).toContain("Total future receipts: ₹12,560/unit");
    expect(p).toContain("=== END DATA ===");
  });

  it("anchors the data with today's date", () => {
    const p = buildBondPrompt(mk(), { today: new Date("2026-08-10") });
    expect(p).toContain("Today: 10 Aug 2026");
  });

  it("carries the verification and framing instructions", () => {
    const p = buildBondPrompt(mk());
    expect(p).toContain("VERIFY");
    expect(p).toContain("independently recompute the YTM");
    expect(p).toContain("0.10 percentage points");
    expect(p).toContain("G-sec");
    expect(p).toContain("taxed at slab");
    expect(p).toContain("do not invent numbers");
  });

  it("flags floating-rate coupons prominently", () => {
    const p = buildBondPrompt(
      mk({ terms: { coupon_type: "Floating", coupon_basis: "MIBOR + 250bps" } }),
    );
    expect(p).toContain("FLOATING RATE");
    expect(p).toContain("MIBOR + 250bps");
    expect(buildBondPrompt(mk())).not.toContain("FLOATING RATE");
  });

  it("shows the day-count convention and put option from terms", () => {
    const p = buildBondPrompt(
      mk({ terms: { day_count_convention: "Actual/Actual", put_indicator: "Yes" } }),
    );
    expect(p).toContain("Day-count convention: Actual/Actual");
    expect(p).toContain("Put option: yes");
  });

  it("includes the liquidity line only when trade stats exist", () => {
    const p = buildBondPrompt(mk({ turnover: 4400000, num_trades: 21, volume: 44 } as never));
    expect(p).toContain("Liquidity (last traded day): turnover ₹44.00 L · 21 trades · 44 units");
    expect(buildBondPrompt(mk())).not.toContain("Liquidity (last traded day)");
  });

  it("renders the order-book snapshot when depth is provided", () => {
    const depth = {
      isin: "INE000TEST01",
      fetched_at: "",
      nse: null,
      bse: {
        bids: [{ price: 9750, qty: 51, orders: 2 }],
        asks: [{ price: 9987, qty: 200, orders: 1 }],
        total_bid_qty: 177,
        total_ask_qty: 250,
        as_of: null,
        source: "kite",
      },
      ytm_at_ask: [{ price: 9987, ytm: 13.9 }],
    } as OrderBook;
    const p = buildBondPrompt(mk(), { depth });
    expect(p).toContain("Order book now (BSE): best bid ₹9,750 × 51, best ask ₹9,987 × 200");
    expect(p).toContain("(total bid 177 / ask 250)");
    expect(p).toContain("YTM at best ask 13.90%");
    expect(buildBondPrompt(mk(), { depth: null })).not.toContain("Order book now");
  });

  it("never leaks null/undefined and omits empty fields", () => {
    const p = buildBondPrompt(
      mk({
        ltp: null,
        clean_price: null,
        accrued: null,
        ytm: null,
        ytm_exact: null,
        current_yield: null,
        duration: null,
        otc_price_pct: null,
        credit_rating: null,
        trade_date: null,
        days_since_traded: null,
        maturity_date: null,
        years_to_maturity: null,
        coupon: null,
        rating_timeline: {},
        cashflows: [],
        next_payment: null,
        future_total_per_unit: 0,
        terms: {},
      }),
    );
    expect(p).not.toMatch(/null|undefined|NaN/);
    expect(p).toContain("Never traded on-exchange");
    expect(p).toContain("Rating: unrated / none on record");
    expect(p).not.toContain("Duration:");
    expect(p).not.toContain("-- Same issuer --");
  });

  it("caps rating history at 10 events", () => {
    const events: RatingEvent[] = Array.from({ length: 20 }, (_, i) => ({
      rating: `BB${i}`,
      outlook: null,
      action: null,
      rating_date: `2025-01-${String(i + 1).padStart(2, "0")}`,
      press_release: null,
    }));
    const p = buildBondPrompt(mk({ rating_timeline: { CRISIL: events } }));
    expect(p.match(/CRISIL BB/g)?.length).toBe(10);
  });

  it("lists every future flow when under the cap, marking estimates", () => {
    const flows: Cashflow[] = [
      ...Array.from({ length: 6 }, (_, i) => ({
        event: "interest" as const,
        due_date: `2027-0${i + 1}-15`,
        record_date: null,
        amount: 110,
        estimated: i === 0,
        future: true,
      })),
      {
        event: "redemption" as const,
        due_date: "2028-12-19",
        record_date: null,
        amount: 10000,
        estimated: false,
        future: true,
      },
    ];
    const p = buildBondPrompt(mk({ cashflows: flows, amounts_estimated: true }));
    expect(p.match(/interest {2}/g)?.length).toBe(6);
    expect(p).toContain("15 Jan 2027  interest  ≈₹110");
    expect(p).toContain("15 Feb 2027  interest  ₹110");
    expect(p).toContain("amounts marked ≈ are estimated");
  });

  it("caps long schedules but always keeps the redemptions", () => {
    const flows: Cashflow[] = [
      ...Array.from({ length: 60 }, (_, i) => ({
        event: "interest" as const,
        due_date: `20${27 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}-15`,
        record_date: null,
        amount: 110,
        estimated: false,
        future: true,
      })),
      {
        event: "redemption" as const,
        due_date: "2032-12-19",
        record_date: null,
        amount: 10000,
        estimated: false,
        future: true,
      },
    ];
    const p = buildBondPrompt(mk({ cashflows: flows }));
    expect(p).toContain("more interest payments");
    expect(p).toContain("19 Dec 2032  redemption  ₹10,000");
    // 38 interest rows kept (40 cap - 1 redemption - 1 ellipsis line)
    expect(p.match(/ {2}interest {2}/g)?.length).toBe(38);
  });

  it("lists rating-rationale and document links, capped at 5 each", () => {
    const events: RatingEvent[] = Array.from({ length: 8 }, (_, i) => ({
      rating: "A",
      outlook: null,
      action: null,
      rating_date: `2025-02-0${i + 1}`,
      press_release: `https://crisil.com/pr/${i}`,
    }));
    const docs = Array.from({ length: 8 }, (_, i) => ({
      doc_name: `Prospectus ${i}`,
      source: "NSDL",
      download_link: `https://nsdl.com/doc/${i}`,
    }));
    const p = buildBondPrompt(
      mk({ rating_timeline: { CRISIL: events }, documents: docs as BondDetail["documents"] }),
    );
    expect(p).toContain("-- Reports & documents --");
    expect(p.match(/Rating rationale:/g)?.length).toBe(5);
    expect(p.match(/Document: Prospectus/g)?.length).toBe(5);
    // Most recent release first
    expect(p).toContain("https://crisil.com/pr/7");
    expect(p).not.toContain("https://crisil.com/pr/0");
  });

  it("includes the issue date in the terms section", () => {
    expect(buildBondPrompt(mk())).toContain("Issued: 19 Dec 2023");
    expect(buildBondPrompt(mk({ issue_date: null }))).not.toContain("Issued:");
  });

  it("describes cumulative bonds via the implied coupon", () => {
    const p = buildBondPrompt(
      mk({
        coupon: null,
        implied_coupon: 11.2,
        frequency: "cumulative",
        schedule_shape: "cumulative",
      }),
    );
    expect(p).toContain("pays everything at maturity (cumulative)");
    expect(p).toContain("≈11.20% effective annual implied");
  });

  it("omits the reports section when there are no links", () => {
    expect(buildBondPrompt(mk({ rating_timeline: {}, documents: [] }))).not.toContain(
      "Reports & documents",
    );
  });

  it("marks what-if prices as hypothetical", () => {
    expect(buildBondPrompt(mk({ custom_price: true }))).toContain("hypothetical what-if");
    expect(buildBondPrompt(mk())).not.toContain("hypothetical");
  });

  it("stays paste-friendly in size", () => {
    expect(buildBondPrompt(mk()).length).toBeLessThan(5000);
  });
});
