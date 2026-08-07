import { describe, expect, it } from "vitest";
import type { PortfolioMonth, PortfolioPosition } from "../api/types";
import { isPastMonth, monthGrid, weights } from "./portfolio";

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
