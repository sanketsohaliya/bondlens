import { describe, expect, it } from "vitest";
import { DEFAULT_FILTERS, describeFilters } from "./FilterBar";

describe("default filters", () => {
  it("starts with investment-grade only — everything else unrestricted", () => {
    expect(DEFAULT_FILTERS).toEqual({ min_rating: "BBB-" });
  });
});

describe("describeFilters", () => {
  it("summarizes the default filter set", () => {
    expect(describeFilters(DEFAULT_FILTERS)).toEqual(["rating ≥ BBB-"]);
  });

  it("covers ranges, ticket size and boolean flags", () => {
    expect(
      describeFilters({
        max_ytm: 12,
        min_years: 2,
        max_years: 5,
        max_ltp: 1500,
        frequency: "monthly",
        payout: "maturity",
        exchange: "NSE",
        fresh_only: true,
        exact_only: true,
        below_par: true,
      }),
    ).toEqual([
      "YTM ≤ 12%",
      "maturity ≥ 2y",
      "maturity ≤ 5y",
      "price ≤ ₹1,500",
      "pays monthly",
      "pays at maturity",
      "on NSE",
      "fresh prices",
      "exact YTM only",
      "below face value",
    ]);
  });

  it("distinguishes retail and OTC turnover", () => {
    expect(describeFilters({ min_turnover: 100000, min_otc_turnover: 10000000 })).toEqual([
      "retail traded ≥ ₹1.00 L",
      "OTC traded ≥ ₹1.00 Cr",
    ]);
  });

  it("covers the segment-presence flags", () => {
    expect(describeFilters({ retail_only: true, otc_only: true })).toEqual([
      "trades retail",
      "trades OTC",
    ]);
  });

  it("ignores the search query and empty params", () => {
    expect(describeFilters({ q: "hdfc" })).toEqual([]);
    expect(describeFilters({})).toEqual([]);
  });
});
