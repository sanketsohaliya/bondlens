import { describe, expect, it } from "vitest";
import type { PortfolioMonth, PortfolioPosition } from "../api/types";
import {
  bondIncome,
  cashAmount,
  isPastMonth,
  monthGrid,
  netInterest,
  portfolioYieldSpread,
  tds,
  weights,
} from "./portfolio";

const cf = (month: string, interest: number, principal = 0): PortfolioMonth => ({
  month,
  interest,
  principal,
  received_interest: 0,
  received_principal: 0,
  estimated: false,
  bonds: [],
});

describe("monthGrid", () => {
  it("buckets months into year rows with totals", () => {
    const rows = monthGrid([cf("2026-03", 500), cf("2026-09", 500), cf("2027-03", 500, 10000)]);
    expect(rows.map((r) => r.year)).toEqual([2026, 2027]);
    expect(rows[0].total).toBe(1000);
    expect(rows[0].months[2]?.interest).toBe(500);
    expect(rows[0].months[8]?.interest).toBe(500);
    expect(rows[0].months[0]).toBeNull();
    expect(rows[1].total).toBe(10500);
  });

  it("empty in, empty out", () => {
    expect(monthGrid([])).toEqual([]);
  });
});

describe("bondIncome", () => {
  const now = new Date(2026, 7, 7); // 7 Aug 2026
  const bond = (isin: string, interest: number, principal = 0) => ({
    isin,
    label: isin,
    interest,
    principal,
    estimated: false,
    dates: [],
  });
  const months: PortfolioMonth[] = [
    { ...cf("2026-05", 100), bonds: [bond("A", 60), bond("B", 40)] },
    { ...cf("2026-08", 50), bonds: [bond("A", 50)] }, // current month
    { ...cf("2027-03", 80, 1000), bonds: [bond("A", 80, 1000)] },
  ];

  it("splits past vs future months for one isin", () => {
    expect(bondIncome(months, "A", now)).toEqual({ received: 60, projected: 1130 });
  });

  it("current month counts as projected", () => {
    expect(bondIncome(months, "A", now).projected).toBe(50 + 80 + 1000);
  });

  it("skips months where the bond does not pay", () => {
    expect(bondIncome(months, "B", now)).toEqual({ received: 40, projected: 0 });
  });

  it("unknown isin and empty input yield zeros", () => {
    expect(bondIncome(months, "Z", now)).toEqual({ received: 0, projected: 0 });
    expect(bondIncome([], "A", now)).toEqual({ received: 0, projected: 0 });
  });
});

describe("isPastMonth", () => {
  const now = new Date(2026, 7, 7); // 7 Aug 2026
  it("month before the current one is past", () => {
    expect(isPastMonth("2026-07", now)).toBe(true);
    expect(isPastMonth("2025-12", now)).toBe(true);
  });
  it("current month still carries projected flows - not past", () => {
    expect(isPastMonth("2026-08", now)).toBe(false);
  });
  it("future months are not past", () => {
    expect(isPastMonth("2026-09", now)).toBe(false);
    expect(isPastMonth("2027-01", now)).toBe(false);
  });
});

const pos = (isin: string, value: number | null, over: Partial<PortfolioPosition> = {}) =>
  ({
    isin,
    issuer_name: `Issuer ${isin}`,
    rating_grade: "AA",
    sector: "Finance",
    value,
    ...over,
  }) as PortfolioPosition;

describe("portfolio yield comparison", () => {
  it("compares effective annual rates, not nominal YTM against XIRR", () => {
    const original = ((1 + 0.1 / 12) ** 12 - 1) * 100;
    const current = ((1 + 0.102 / 12) ** 12 - 1) * 100;
    const p = pos("A", 1000, { ytm: 10.2, ytm_effective: current, yield_on_cost: original });
    expect(portfolioYieldSpread(p)).toBeCloseTo(0.219315947, 7);
  });

  it("does not fall back to an unconverted nominal rate", () => {
    expect(portfolioYieldSpread(pos("A", 1000, { ytm: 10.2, yield_on_cost: 10.47 }))).toBeNull();
    expect(
      portfolioYieldSpread(pos("A", 1000, { ytm_effective: 10.69, yield_on_cost: null })),
    ).toBeNull();
    expect(
      portfolioYieldSpread(pos("A", 1000, { ytm_effective: Number.NaN, yield_on_cost: 10 })),
    ).toBeNull();
  });
});

describe("weights", () => {
  it("computes percentage weights by issuer", () => {
    const slices = weights([pos("A", 7500), pos("B", 2500)], "issuer");
    expect(slices.map((s) => s.pct)).toEqual([75, 25]);
    expect(slices[0].isins).toEqual(["A"]);
  });

  it("groups by rating with Unrated fallback and merges", () => {
    const slices = weights(
      [pos("A", 500), pos("B", 300, { rating_grade: null }), pos("C", 200)],
      "rating",
    );
    expect(slices[0]).toMatchObject({ label: "AA", pct: 70, isins: ["A", "C"] });
    expect(slices[1]).toMatchObject({ label: "Unrated", pct: 30 });
  });

  it("folds beyond top-N into Others", () => {
    const positions = Array.from({ length: 10 }, (_, i) => pos(`P${i}`, 100 - i));
    const slices = weights(positions, "issuer", 8);
    expect(slices).toHaveLength(9);
    const others = slices.at(-1);
    expect(others?.label).toBe("Others");
    expect(others?.isins).toEqual(["P8", "P9"]);
    expect(slices.reduce((s, x) => s + x.pct, 0)).toBeCloseTo(100, 0);
  });

  it("skips zero/null values and handles empty", () => {
    expect(weights([pos("A", null), pos("B", 0)], "sector")).toEqual([]);
    expect(weights([], "sector")).toEqual([]);
  });
});

describe("TDS on interest", () => {
  it("is 10% of interest and never touches principal", () => {
    expect(tds(2252)).toBeCloseTo(225.2);
    expect(netInterest(2252)).toBeCloseTo(2026.8);
    expect(cashAmount({ interest: 2252, principal: 10000 }, false)).toBe(12252);
    expect(cashAmount({ interest: 2252, principal: 10000 }, true)).toBeCloseTo(12026.8);
  });
});
