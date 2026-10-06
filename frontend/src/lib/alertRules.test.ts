import { describe, expect, it } from "vitest";
import type { OpportunityHit } from "../api/types";
import {
  describeConditions,
  describeRuleFilters,
  groupHits,
  parseDeepLink,
  splitFilters,
  suggestRuleName,
  watcherStatus,
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
    expect(suggestRuleName({ min_rating: "BBB-", min_years: 1 }, "12")).toBe(
      "BBB-, 1y+, ask ≥ 12%",
    );
    expect(suggestRuleName({ min_years: 0.5, max_years: 3, payout: "bullet" }, "", "1050")).toBe(
      "6mo–3y, bullet, price ≤ ₹1,050",
    );
    expect(suggestRuleName({ max_years: 2, exchange: "NSE" }, "9.5")).toBe(
      "≤2y, on NSE, ask ≥ 9.5%",
    );
    expect(suggestRuleName({}, "")).toBe("all bonds");
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

function hit(over: Partial<OpportunityHit>): OpportunityHit {
  return {
    id: 1,
    source: "rule:4",
    isin: "INE000A",
    side: "ask",
    price: 997,
    qty: 10,
    ytm: 15.95,
    exchange: "BSE",
    trade_day: "2026-10-06",
    tick_at: null,
    fired_at: "2026-10-06T08:04:00+00:00",
    pushed_at: null,
    push_ms: 50,
    below_threshold: null,
    issuer_name: "ICL FINCORP LIMITED",
    nse_symbol: null,
    bse_scrip_id: null,
    ...over,
  };
}

describe("hits grouped per bond per day", () => {
  it("keeps the latest quote, counts, and the best yield", () => {
    const groups = groupHits([
      hit({ id: 1, fired_at: "2026-10-06T07:36:00+00:00", ytm: 14.7, price: 1112 }),
      hit({ id: 2, fired_at: "2026-10-06T08:04:00+00:00", ytm: 15.24, price: 1080 }),
      hit({
        id: 3,
        isin: "INE000B",
        issuer_name: "FINKURVE",
        fired_at: "2026-10-06T08:05:00+00:00",
      }),
      hit({ id: 4, trade_day: "2026-10-05", fired_at: "2026-10-05T08:00:00+00:00" }),
    ]);
    expect(groups.map((g) => [g.name, g.count, g.latest.id, g.bestYtm])).toEqual([
      ["FINKURVE", 1, 3, 15.95],
      ["ICL FINCORP LIMITED", 2, 2, 15.24],
      ["ICL FINCORP LIMITED", 1, 4, 15.95],
    ]);
  });

  it("marks a group short only when every hit fell short", () => {
    const [g] = groupHits([
      hit({ id: 1, below_threshold: 0.3 }),
      hit({ id: 2, below_threshold: null }),
    ]);
    expect(g.short).toBe(false);
    expect(groupHits([hit({ below_threshold: 0.3 })])[0].short).toBe(true);
  });
});

describe("watcher status line", () => {
  const now = new Date("2026-10-06T08:00:00+00:00").getTime();
  const beat = (secondsAgo: number, note: string, tokens = 5759) => ({
    beat_at: new Date(now - secondsAgo * 1000).toISOString(),
    note,
    tokens,
  });
  it("reads the heartbeat", () => {
    expect(watcherStatus(beat(20, "open; 1 user(s)"), 2, true, now)).toEqual({
      label: "live · 5,759 instruments",
      tone: "live",
    });
    expect(watcherStatus(beat(20, "idle"), 2, true, now).tone).toBe("idle");
    expect(watcherStatus(beat(20, "open; 1 user(s)"), 2, false, now)).toEqual({
      label: "Kite not connected",
      tone: "warn",
    });
    expect(watcherStatus(beat(600, "open; 1 user(s)"), 2, true, now).label).toBe("watcher silent");
    expect(watcherStatus(null, 0, true, now).tone).toBe("idle");
    expect(watcherStatus(null, 1, true, now).tone).toBe("warn");
  });
});
