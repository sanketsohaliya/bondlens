import { describe, expect, it } from "vitest";
import { fmt } from "./client";

describe("fmt.num", () => {
  it("formats with Indian grouping", () => {
    expect(fmt.num(1234567.891)).toBe("12,34,567.89");
  });
  it("renders null as dash", () => {
    expect(fmt.num(null)).toBe("–");
    expect(fmt.num(undefined)).toBe("–");
  });
});

describe("fmt.inr", () => {
  it("uses crore for >= 1e7", () => {
    expect(fmt.inr(2.5e7)).toBe("₹2.50Cr");
  });
  it("uses lakh for >= 1e5", () => {
    expect(fmt.inr(150000)).toBe("₹1.50L");
  });
  it("plain rupees below a lakh", () => {
    expect(fmt.inr(99999)).toBe("₹99,999");
  });
  it("renders null as dash", () => {
    expect(fmt.inr(null)).toBe("–");
  });
});

describe("fmt.pct", () => {
  it("two decimals with % suffix", () => {
    expect(fmt.pct(9.876)).toBe("9.88%");
  });
  it("renders null as dash", () => {
    expect(fmt.pct(null)).toBe("–");
  });
});

describe("fmt.date", () => {
  it("formats ISO dates", () => {
    expect(fmt.date("2026-07-12")).toMatch(/12 Jul 2026/);
  });
  it("renders null as dash", () => {
    expect(fmt.date(null)).toBe("–");
  });
});
