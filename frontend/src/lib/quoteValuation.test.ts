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
    // The caveat survives on hover in its terse form, not the raw sentence.
    const tip = valuationTooltip(value);
    expect(tip).toContain("⚠ Yield to call");
    expect(tip).toContain("exercises the call");
    expect(tip.split("\n")).toHaveLength(2);
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
    // Legacy snapshot (no structured gap): the stored sentence is the one caveat.
    expect(html).toContain("⚠ OTC 5.00% on 2026-09-11");
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

  it("phrases the OTC comparison by direction with a matching action", async () => {
    const { comparisonText } = await import("./quoteValuation");
    const above = comparisonText(
      {
        code: "otc_difference",
        severity: "info",
        message: "legacy",
        otc_yield: 12.26,
        otc_trade_date: "2026-09-11",
        gap: 6.0,
      },
      18.26,
    );
    expect(above.headline).toBe(
      "Retail 18.26% vs OTC 12.26% (11 Sept 2026) — 6.0 pts above institutional.",
    );
    expect(above.action).toBe(
      "Looks too good: 6.0 pts above what institutions paid — the retail price is probably stale. Check the order book.",
    );
    expect(above.tone).toBe("attention");
    const below = comparisonText(
      {
        code: "otc_difference",
        severity: "info",
        message: "legacy",
        otc_yield: 12.26,
        otc_trade_date: "2026-09-11",
        gap: -3.16,
      },
      9.1,
    );
    expect(below.headline).toBe(
      "Retail 9.10% vs OTC 12.26% (11 Sept 2026) — 3.2 pts below institutional.",
    );
    expect(below.action).toBe(
      "You'd get 3.2 pts less yield than institutions did — check the ask before buying.",
    );
    expect(below.tone).toBe("attention");
    // Old snapshot without structured fields: fall back to the stored sentence.
    const legacy = comparisonText(
      { code: "otc_difference", severity: "info", message: "OTC 5.00% on 2026-09-11" },
      10,
    );
    expect(legacy.headline).toBe("OTC 5.00% on 2026-09-11");
    expect(legacy.action).toBeNull();
    expect(legacy.tone).toBe("neutral");
  });

  it("keeps the hover tooltip to a gain line plus at most one caveat", () => {
    const base = {
      ...quote(),
      gross_gain: 1540,
      holding_period_return: 16.3,
      day_count: "act/act",
      periods_per_year: 12,
      price_basis: "Dirty price",
      settlement_assumption: "T+1",
      calculation_version: "v",
    };
    const plain = valuationTooltip(base).split("\n");
    expect(plain).toEqual(["Earn ₹1,540 per unit (16.30%) if held to maturity, before tax."]);
    const withOtc = valuationTooltip({
      ...base,
      warnings: [
        { code: "short_horizon", severity: "info", message: "Only 40 days" },
        {
          code: "otc_difference",
          severity: "info",
          message: "legacy",
          otc_yield: 12.26,
          otc_trade_date: "2026-09-11",
          gap: 6.0,
        },
        { code: "yield_range", severity: "caution", message: "Yield outside sanity range" },
      ],
    }).split("\n");
    expect(withOtc).toHaveLength(2);
    expect(withOtc[1]).toBe(
      "⚠ Looks too good: 6.0 pts above what institutions paid — the retail price is probably stale. Check the order book.",
    );
    // Large gaps read as whole points; the row hover and the drawer share one sentence.
    const big = valuationTooltip({
      ...base,
      warnings: [
        {
          code: "otc_difference",
          severity: "info",
          message: "x",
          otc_yield: 5,
          otc_trade_date: "2026-09-11",
          gap: 12.4,
        },
      ],
    }).split("\n");
    expect(big[1]).toBe(
      "⚠ Looks too good: 12 pts above what institutions paid — the retail price is probably stale. Check the order book.",
    );
    const variable = valuationTooltip({
      ...base,
      warnings: [{ code: "variable_coupon", severity: "caution", message: "long text" }],
    }).split("\n");
    expect(variable[1]).toBe("⚠ Floating coupon — yield assumes today's rate.");
    const ytc = valuationTooltip({ ...base, yield_type: "ytc" as const }).split("\n");
    expect(ytc[0]).toContain("if held to first call");
    const noGain = valuationTooltip({ ...quote(), gross_gain: null }).split("\n");
    expect(noGain).toEqual(["Indicative YTM at the displayed price."]);
    for (const line of [...plain, ...withOtc, ...variable]) {
      expect(line).not.toMatch(/Day count|compounded|Calculator|Valuation date|settlement/i);
    }
  });

  it("keeps every provenance line available for the drawer", async () => {
    const { provenanceLines } = await import("./quoteValuation");
    const lines = provenanceLines({
      ...quote(),
      day_count: "act/act",
      periods_per_year: 12,
      price_basis: "Dirty price",
      settlement_assumption: "T+1 settlement",
    });
    expect(lines.join("\n")).toMatch(/Valuation date: 2026-09-12/);
    expect(lines.join("\n")).toMatch(/Dirty price/);
    expect(lines.join("\n")).toMatch(/compounded 12/);
    expect(lines.join("\n")).toMatch(/Actual\/Actual/);
    expect(lines.join("\n")).toMatch(/T\+1 settlement/);
    expect(lines.join("\n")).toMatch(/Calculator: test/);
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
