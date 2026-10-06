import { describe, expect, it } from "vitest";
import {
  describeConditions,
  describeRuleFilters,
  parseDeepLink,
  splitFilters,
  suggestRuleName,
} from "./alertRules";

describe("alert rules from the filter bar", () => {
  it("keeps bond descriptors and names the trade-history filters it drops", () => {
    const { kept, dropped } = splitFilters({
      min_rating: "BBB-",
      min_years: 0.5,
      payout: "bullet",
      min_ytm: 5,
      min_turnover: 10_000,
      traded_within: 5,
      sector: "",
      q: "axis",
    });
    expect(kept).toEqual({ min_rating: "BBB-", min_years: 0.5, payout: "bullet" });
    expect(dropped).toEqual(["Min LTP YTM", "Retail turnover", "Last traded"]);
  });

  it("describes filters and conditions tersely", () => {
    expect(describeRuleFilters({ min_rating: "AA-", max_years: 3, payout: "bullet" })).toBe(
      "rating ≥ AA- · maturity ≤ 3y · bullet",
    );
    expect(describeRuleFilters({})).toBe("every listed bond");
    expect(
      describeConditions({ min_ask_ytm: 12, max_ticket: 200_000, max_price: null, min_qty: 5 }),
    ).toBe("YTM @ Ask ≥ 12% · ticket ≤ ₹2.00 L · ≥ 5 units");
    expect(suggestRuleName({ min_rating: "BBB-" }, "12")).toBe("rating ≥ BBB- ≥ 12%");
    expect(suggestRuleName({ sector: "x".repeat(80) }, "").length).toBeLessThanOrEqual(60);
  });

  it("parses the push deep link and rejects junk", () => {
    expect(parseDeepLink("?bond=ine000a01001&tab=order%20book")).toEqual({
      isin: "INE000A01001",
      tab: "order book",
    });
    expect(parseDeepLink("?bond=INE000A01001")).toEqual({ isin: "INE000A01001", tab: undefined });
    expect(parseDeepLink("?bond=<script>")).toBeNull();
    expect(parseDeepLink("")).toBeNull();
  });
});
