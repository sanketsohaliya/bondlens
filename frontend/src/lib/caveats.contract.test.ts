import { describe, expect, it } from "vitest";
// Vite's ?raw import hands us the Python source as a string, typed by
// vite/client - no Node types needed in a browser-targeted tsconfig.
import valuationSource from "../../../backend/analytics/valuation.py?raw";
import { CAVEATS } from "./quoteValuation";

/** The backend and the hover text must stay in step: every warning code the
 * valuation engine can emit needs a sentence here, or the \u26a0 badge can appear
 * with nothing to say (13 Sept 2026: old_price lit 60% of rows silently).
 * Reading the Python source is unusual, but the two lists have no other
 * shared artifact and drift is exactly the failure mode. */
describe("warning caveats contract", () => {
  const backendCodes = [...valuationSource.matchAll(/warning\(\s*"([a-z_]+)"/g)].map((m) => m[1]);

  it("finds the backend's warning codes", () => {
    expect(backendCodes.length).toBeGreaterThanOrEqual(15);
    expect(backendCodes).toContain("old_price");
    expect(backendCodes).toContain("otc_difference");
  });

  it("has a caveat entry for every backend warning code", () => {
    const missing = [...new Set(backendCodes)].filter((code) => !(code in CAVEATS));
    expect(missing, `add these to CAVEATS in quoteValuation.ts: ${missing.join(", ")}`).toEqual([]);
  });

  it("has no stale caveat entries for codes the backend no longer emits", () => {
    const stale = Object.keys(CAVEATS).filter((code) => !backendCodes.includes(code));
    expect(stale, `remove from CAVEATS: ${stale.join(", ")}`).toEqual([]);
  });
});
