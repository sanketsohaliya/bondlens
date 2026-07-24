import { describe, expect, it } from "vitest";
import type { BondDetail, Cashflow, RatingEvent } from "../api/types";
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
    expect(p).toContain("₹10,000 on 19 Dec 2028");
    expect(p).toContain("Total future receipts: ₹12,560/unit");
    expect(p).toContain("=== END DATA ===");
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

  it("caps rating history and redemption lists", () => {
    const events: RatingEvent[] = Array.from({ length: 20 }, (_, i) => ({
      rating: `BB${i}`,
      outlook: null,
      action: null,
      rating_date: `2025-01-${String(i + 1).padStart(2, "0")}`,
      press_release: null,
    }));
    const flows: Cashflow[] = Array.from({ length: 20 }, (_, i) => ({
      event: "redemption" as const,
      due_date: `2027-01-${String(i + 1).padStart(2, "0")}`,
      record_date: null,
      amount: 500,
      estimated: false,
      future: true,
    }));
    const p = buildBondPrompt(mk({ rating_timeline: { CRISIL: events }, cashflows: flows }));
    expect(p.match(/CRISIL BB/g)?.length).toBe(10);
    expect(p.match(/₹500 on/g)?.length).toBe(12);
    expect(p).toContain("(+8 more)");
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
