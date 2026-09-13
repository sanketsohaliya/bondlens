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

  it("does not badge an OTC-only note, including old snapshots", () => {
    // The backend always emits otc_difference as info: it is market context.
    const severity = "info" as const;
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
    // Legacy snapshot (no structured gap): the stored sentence is the one
    // caveat, with the info glyph because it is context, not a warning.
    expect(html).toContain("ⓘ OTC 5.00% on 2026-09-11");
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

  it("states the OTC comparison as a rupee price gap, in the units of the LTP", async () => {
    const { comparisonText } = await import("./quoteValuation");
    const note = (extra: object) => ({
      code: "otc_difference",
      severity: "info" as const,
      message: "legacy",
      ...extra,
    });
    const above = comparisonText(
      note({
        otc_yield: 12.26,
        otc_trade_date: "2026-09-11",
        gap: 6.0,
        otc_price: 9950,
        retail_price: 9425,
        price_gap: -525,
        price_gap_pct: -5.28,
      }),
      18.26,
    );
    expect(above.headline).toBe(
      "Retail 18.26% vs OTC 12.26% (11 Sept 2026) — 6.0 pts above institutional.",
    );
    // The action is the price gap the reader can compare with the LTP, not an instruction to go look elsewhere.
    expect(above.action).toBe(
      "Institutions paid ≈₹9,950 for this on 11 Sept 2026; retail last traded at ₹9,425 — ₹525 (5.3%) cheaper.",
    );
    expect(above.action).not.toMatch(/stale|order book/);
    // Dirty prices carry paise; prose rounds to whole rupees.
    const fractional = comparisonText(
      note({
        otc_yield: 12.26,
        otc_trade_date: "2026-09-11",
        gap: 6.0,
        otc_price: 9950.4,
        retail_price: 9425,
        price_gap: -525.4,
        price_gap_pct: -5.28,
      }),
      18.26,
    );
    expect(fractional.action).toContain("≈₹9,950 for this");
    expect(fractional.action).toContain("₹525 (5.3%) cheaper");
    expect(above.tone).toBe("attention");
    const below = comparisonText(
      note({
        otc_yield: 12.26,
        otc_trade_date: "2026-09-11",
        gap: -3.16,
        otc_price: 9950,
        retail_price: 10300,
        price_gap: 350,
        price_gap_pct: 3.52,
      }),
      9.1,
    );
    expect(below.headline).toBe(
      "Retail 9.10% vs OTC 12.26% (11 Sept 2026) — 3.2 pts below institutional.",
    );
    expect(below.action).toBe(
      "Institutions paid ≈₹9,950 for this on 11 Sept 2026; retail last traded at ₹10,300 — ₹350 (3.5%) more.",
    );
    expect(below.tone).toBe("attention");
    // No rupee data (accrued unknowable): headline only, no filler action.
    const yieldOnly = comparisonText(
      note({ otc_yield: 12.26, otc_trade_date: "2026-09-11", gap: 6.0 }),
      18.26,
    );
    expect(yieldOnly.headline).toContain("6.0 pts above institutional");
    expect(yieldOnly.action).toBeNull();
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
          otc_price: 9950,
          retail_price: 9425,
          price_gap: -525,
          price_gap_pct: -5.28,
        },
        { code: "yield_range", severity: "caution", message: "Yield outside sanity range" },
      ],
    }).split("\n");
    expect(withOtc).toHaveLength(2);
    // A caution about the number itself outranks market context.
    expect(withOtc[1]).toBe("⚠ Yield is outside the sanity range — verify the price.");
    const otcOnly = valuationTooltip({
      ...base,
      warnings: [
        {
          code: "otc_difference",
          severity: "info",
          message: "legacy",
          otc_yield: 12.26,
          otc_trade_date: "2026-09-11",
          gap: 6.0,
          otc_price: 9950,
          retail_price: 9425,
          price_gap: -525,
          price_gap_pct: -5.28,
        },
      ],
    }).split("\n");
    expect(otcOnly[1]).toBe(
      "ⓘ Institutions paid ≈₹9,950 for this on 11 Sept 2026; retail last traded at ₹9,425 — ₹525 (5.3%) cheaper.",
    );
    // Without rupee data the one caveat falls back to the yield gap, still exactly one line.
    const yieldOnly = valuationTooltip({
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
    expect(yieldOnly).toHaveLength(2);
    expect(yieldOnly[1]).toBe("ⓘ Retail yield is 12 pts above the recent OTC print.");
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

  it("badges on severity, not on code: info is context, caution/error is doubt", async () => {
    const { CAVEATS, caveatText, primaryCaveat } = await import("./quoteValuation");
    const v = (warnings: object[]) => ({ ...quote(), warnings }) as never;
    const note = (code: string, severity: "info" | "caution" | "error", message = "m") => ({
      code,
      severity,
      message,
    });
    // 13 Sept 2026: old_price lit ⚠ on 60% of rows while the tooltip said "Earn ₹965".
    expect(
      hasYieldReview(
        v([note("old_price", "info", "Price observation is 37 days old; not current")]),
      ),
    ).toBe(false);
    expect(hasYieldReview(v([note("short_horizon", "info")]))).toBe(false);
    expect(hasYieldReview(v([note("otc_difference", "info")]))).toBe(false);
    expect(hasYieldReview(v([note("old_price", "info"), note("yield_range", "caution")]))).toBe(
      true,
    );
    expect(hasYieldReview(v([note("schedule_invalid", "error")]))).toBe(true);
    // Tooltip glyph agrees with the badge state.
    const stale = valuationTooltip(
      v([
        note(
          "old_price",
          "info",
          "Price observation is 37 days old; it is not a current executable offer",
        ),
      ]),
    ).split("\n");
    expect(stale[1]).toBe("ⓘ Last trade 37 days ago — not a current offer.");
    // Caution outranks info regardless of map order.
    expect(
      primaryCaveat(v([note("old_price", "info"), note("estimated_payments", "caution")]))?.code,
    ).toBe("estimated_payments");
    // An unknown code is never silent: the backend's own sentence shows, with ⚠.
    const unknown = valuationTooltip(
      v([note("zzz_new_code", "caution", "Something new went wrong")]),
    ).split("\n");
    expect(unknown[1]).toBe("⚠ Something new went wrong");
    expect(caveatText(note("zzz_new_code", "caution", "Something new went wrong"))).toBe(
      "Something new went wrong",
    );
    // Every explained code has a real sentence or is composed elsewhere.
    for (const [code, text] of Object.entries(CAVEATS)) {
      if (text !== null) expect(text.length).toBeGreaterThan(10);
      else expect(["otc_difference", "old_price"]).toContain(code);
    }
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
