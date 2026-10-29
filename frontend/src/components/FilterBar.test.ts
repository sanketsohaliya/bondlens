import { describe, expect, it } from "vitest";
import { DEFAULT_FIELD_IDS, DEFAULT_FILTERS, describeFilters, minYearsOptions } from "./FilterBar";

describe("default filters", () => {
  it("starts with rating and tenor only", () => {
    expect(DEFAULT_FILTERS).toEqual({ min_rating: "BBB-", min_years: 0.5 });
  });

  it("only visible filters apply: every default value belongs to a default box", () => {
    for (const key of Object.keys(DEFAULT_FILTERS)) {
      expect(DEFAULT_FIELD_IDS as readonly string[]).toContain(key);
    }
  });

  it("offers 1, 2 and 3 months of minimum maturity", () => {
    const labels = minYearsOptions.map((o) => o.label);
    expect(labels.slice(0, 4)).toEqual(["≥ 1mo", "≥ 2mo", "≥ 3mo", "≥ 6mo"]);
    expect(minYearsOptions.slice(0, 3).map((o) => o.v)).toEqual([0.08, 0.17, 0.25]);
  });
});

describe("describeFilters", () => {
  it("summarizes the default filter set", () => {
    expect(describeFilters(DEFAULT_FILTERS)).toEqual(["rating ≥ BBB-", "maturity ≥ 0.5y"]);
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
        below_par: true,
      }),
    ).toEqual([
      "LTP YTM ≤ 12%",
      "maturity ≥ 2y",
      "maturity ≤ 5y",
      "price ≤ ₹1,500",
      "pays monthly",
      "pays at maturity",
      "on NSE",
      "fresh prices",
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
