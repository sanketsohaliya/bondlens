import { describe, expect, it } from "vitest";
import { DEFAULT_PRESET, describeFilters, PRESETS } from "./FilterBar";

describe("v1 strategy presets", () => {
  it("has the 13 v1 strategies", () => {
    expect(PRESETS).toHaveLength(13);
    expect(PRESETS.map((p) => p.id)).toContain("fdbeater");
  });

  it("FD Beater is the default preset with v1 thresholds", () => {
    expect(DEFAULT_PRESET.id).toBe("fdbeater");
    expect(DEFAULT_PRESET.params).toEqual({
      min_rating: "AA-",
      min_ytm: 8,
      min_turnover: 100000,
    });
  });

  it("every preset has a tooltip and valid params", () => {
    for (const p of PRESETS) {
      expect(p.tip.length).toBeGreaterThan(10);
      expect(Object.keys(p.params).length).toBeGreaterThan(0);
    }
  });
});

describe("describeFilters", () => {
  it("summarizes FD Beater like v1", () => {
    expect(describeFilters(DEFAULT_PRESET.params)).toEqual([
      "rating ≥ AA-",
      "YTM ≥ 8%",
      "retail traded ≥ ₹1.00L",
    ]);
  });

  it("covers ranges, ticket size and boolean flags", () => {
    expect(
      describeFilters({
        max_ytm: 12,
        min_years: 2,
        max_years: 5,
        max_ltp: 1500,
        frequency: "monthly",
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
      "on NSE",
      "fresh prices",
      "exact YTM only",
      "below face value",
    ]);
  });

  it("distinguishes retail and OTC turnover", () => {
    expect(describeFilters({ min_turnover: 100000, min_otc_turnover: 10000000 })).toEqual([
      "retail traded ≥ ₹1.00L",
      "OTC traded ≥ ₹1.00Cr",
    ]);
  });

  it("ignores the search query and empty params", () => {
    expect(describeFilters({ q: "hdfc" })).toEqual([]);
    expect(describeFilters({})).toEqual([]);
  });
});
