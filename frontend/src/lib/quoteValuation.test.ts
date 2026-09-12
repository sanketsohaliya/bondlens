import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { loadAskSnapshot, saveAskSnapshot } from "../components/ExploreTable";
import YieldQuote, { YieldNotes } from "../components/YieldQuote";
import {
  hasYieldReview,
  isQuoteFresh,
  valuationIsCurrent,
  valuationLabel,
  valuationTooltip,
} from "./quoteValuation";

const now = Date.parse("2026-09-12T10:00:00Z");
const quote = (isin = "TEST", time = now) => ({
  isin,
  ask_price: 1000,
  ask_qty: 10,
  ytm: 10,
  as_of: "2026-09-12",
  calculation_version: "test",
  calculated_at: new Date(time).toISOString(),
  fetched_at: new Date(time).toISOString(),
  expires_at: new Date(time + 60_000).toISOString(),
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("quote valuation", () => {
  it("expires both quotes and calculation contexts", () => {
    expect(isQuoteFresh(quote(), now)).toBe(true);
    expect(isQuoteFresh(quote(), now + 61_000)).toBe(false);
    expect(valuationIsCurrent(quote(), now)).toBe(true);
    expect(valuationIsCurrent({ ...quote(), as_of: "2026-09-11" }, now)).toBe(false);
    expect(valuationIsCurrent(quote(), now + 91_000)).toBe(false);
    expect(
      isQuoteFresh({ ...quote(), fetched_at: new Date(now + 3600_000).toISOString() }, now),
    ).toBe(false);
  });

  it("preserves conditional type, estimates and the actual caution reason", () => {
    const value = {
      ...quote(),
      yield_type: "ytc" as const,
      estimated: true,
      warnings: [
        { code: "call_scenario", severity: "caution" as const, message: "Assumes exercise at par" },
      ],
    };
    expect(valuationLabel(value)).toBe("YTC (estimated)");
    expect(valuationTooltip(value)).toContain("Assumes exercise at par");
  });

  it.each([
    "caution",
    "info",
  ] as const)("does not badge an OTC-only %s note, including old snapshots", (severity) => {
    const value = {
      ...quote(),
      ytm_reliable: false,
      warnings: [
        {
          code: "otc_difference",
          severity,
          message: "OTC 5.00% on 2026-09-11; different price basis",
        },
      ],
    };
    const html = renderToStaticMarkup(createElement(YieldQuote, { value }));
    expect(html).toContain("10.00%");
    expect(html).toContain("OTC 5.00%");
    expect(html).not.toContain("text-amber");
    expect(html).not.toContain(">review<");
    expect(hasYieldReview(value, false)).toBe(false);
    const notes = renderToStaticMarkup(createElement(YieldNotes, { value }));
    expect(notes).toContain("Market comparison");
    expect(notes).toContain("2026-09-11");
    expect(notes).not.toContain("text-amber");
    expect(notes).not.toContain("Yield review notes");
  });

  it.each([
    ["estimated_payments", "caution"],
    ["yield_range", "caution"],
    ["future_otc_date", "caution"],
    ["schedule_invalid", "error"],
    ["short_horizon", "info"],
  ] as const)("preserves %s beside an OTC comparison", (code, severity) => {
    const value = {
      ...quote(),
      estimated: true,
      warnings: [
        { code: "otc_difference", severity: "info" as const, message: "OTC comparison data" },
        { code, severity, message: "Actual review reason" },
      ],
    };
    expect(hasYieldReview(value)).toBe(true);
    const badge = renderToStaticMarkup(createElement(YieldQuote, { value }));
    expect(badge).toContain("~10.00%");
    expect(badge).toContain(">review<");
    const notes = renderToStaticMarkup(createElement(YieldNotes, { value }));
    const [review, comparison] = notes.split("</details>");
    expect(review).toContain("Yield review notes (1)");
    expect(review).toContain("Actual review reason");
    expect(review).not.toContain("OTC comparison data");
    expect(comparison).toContain("Market comparison");
    expect(comparison).toContain("OTC comparison data");
    expect(comparison).not.toContain("text-amber");
  });

  it("retains unexplained legacy caution flags rather than assuming they were OTC-only", () => {
    expect(hasYieldReview(null, false)).toBe(true);
    expect(hasYieldReview({ ...quote(), warnings: [] }, false)).toBe(true);
    expect(hasYieldReview(null, true)).toBe(false);
  });

  it("does not rejuvenate old entries when a fresh subset is merged", () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const values = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    });
    saveAskSnapshot(1, [quote("A")], ["A"]);
    vi.setSystemTime(now + 50_000);
    saveAskSnapshot(1, [quote("B", now + 50_000)], ["B"]);
    vi.setSystemTime(now + 65_000);
    expect([...(loadAskSnapshot(1)?.keys() ?? [])]).toEqual(["B"]);
    expect(loadAskSnapshot(2)).toBeNull();
    saveAskSnapshot(1, [], ["B"]);
    expect(loadAskSnapshot(1)).toBeNull();
  });
});
