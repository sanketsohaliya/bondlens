import { afterEach, describe, expect, it, vi } from "vitest";
import type { AskYtm } from "../api/types";
import {
  absReturnText,
  askTicket,
  askTicketText,
  otcLine,
  paymentsLeftText,
  payoutDisplay,
  payoutStructureNote,
  shouldOpenRow,
  watchPayoutDay,
} from "./exploreAmounts";

const quote: AskYtm = { isin: "TEST", ask_price: 1005, ask_qty: 10, ytm: 12.34 };
const payout = {
  payout_total_per_unit: 12560,
  payout_total_state: "known" as const,
  payout_as_of: "2026-09-11",
  payout_valid_until: "2026-09-18",
  coupon: 11.6,
  coupon_basis: "Fixed Interest",
  frequency: "monthly",
  schedule_shape: "staggered",
  future_flows: 21,
  total_flows: 29,
};

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("explore amounts", () => {
  it("shows the entire selected best-level ticket, not one unit or a minimum lot", () => {
    expect(askTicket(quote)).toBe(10050);
    expect(askTicketText(quote).label).toBe("₹1,005 (₹10,050)");
    expect(askTicketText({ ...quote, ask_price: 10025 }).label).toBe("₹10,025 (₹1.00 L)");
    expect(askTicketText({ ...quote, ask_price: 10025 }).detail).toContain("₹1,00,250");
    expect(askTicketText({ ...quote, ask_price: 10025 }).detail).toContain("not a minimum");
  });

  it("rejects unavailable or invalid size but does not require a YTM", () => {
    for (const qty of [null, 0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(askTicket({ ...quote, ask_qty: qty })).toBeNull();
    }
    expect(askTicket({ ...quote, ask_price: 1e308, ask_qty: 10 })).toBeNull();
    expect(askTicket({ ...quote, ask_price: 0 })).toBeNull();
    expect(askTicket({ ...quote, ytm: null })).toBe(10050);
    expect(askTicket(null)).toBeNull();
  });

  it("preserves fractional-price arithmetic and existing Indian units", () => {
    expect(askTicket({ ...quote, ask_price: 1000.25, ask_qty: 10 })).toBe(10002.5);
    expect(askTicketText({ ...quote, ask_price: 250000, ask_qty: 100 }).label).toBe(
      "₹2,50,000 (₹2.50 Cr)",
    );
    expect(askTicketText({ ...quote, ask_qty: null }).label).toBe("₹1,005 (size unavailable)");
    expect(askTicketText({ ...quote, ask_price: 0 }).label).toBe("–");
  });

  it("labels current per-unit totals, estimates and known zero", () => {
    expect(payoutDisplay(payout, "2026-09-11").label).toBe("₹12,560");
    expect(payoutDisplay({ ...payout, payout_total_state: "estimated" }, "2026-09-11").label).toBe(
      "~₹12,560",
    );
    expect(payoutDisplay({ ...payout, payout_total_per_unit: 0 }, "2026-09-11").label).toBe("₹0");
    expect(payoutDisplay(payout, "2026-09-11").detail).toContain("per unit");
  });

  it("explains the payout in plain, line-broken English led by the rupee total", () => {
    const lines = payoutDisplay(payout, "2026-09-11").detail.split("\n");
    expect(lines).toEqual([
      "You'd receive ₹12,560 per unit if you buy now and hold to maturity — 21 of 29 payments still to come.",
      "11.6% fixed interest, paid monthly · principal repaid in parts, not one lump sum at maturity",
      "Before tax & fees · gross cash, not profit or a guaranteed return.",
      "Schedule per NSDL as of 11 Sept 2026 · click to see every payment.",
    ]);
    const detail = lines.join("\n");
    for (const jargon of [
      "modeled",
      "buyer-entitlement",
      "present value",
      "staggered",
      "Fixed Interest",
    ])
      expect(detail).not.toContain(jargon);
    const est = payoutDisplay({ ...payout, payout_total_state: "estimated" }, "2026-09-11").detail;
    expect(est).toContain("about ₹12,560");
    expect(est).toContain("estimated");
    expect(
      payoutDisplay({ ...payout, payout_total_state: "open_ended" }, "2026-09-11").detail,
    ).toContain("no maturity");
    expect(
      payoutDisplay({ ...payout, schedule_shape: "perpetual" }, "2026-09-11").detail,
    ).toContain("hold to the first call");
    expect(payoutDisplay({}, "2026-09-11").detail).toBe(
      "Payout total not available yet.\nClick to see every payment.",
    );
  });

  it("describes structure and remaining payments without raw tokens", () => {
    expect(payoutStructureNote(payout)).toBe(
      "11.6% fixed interest, paid monthly · principal repaid in parts, not one lump sum at maturity",
    );
    expect(
      payoutStructureNote({ coupon: 9.5, coupon_basis: "Variable Interest", frequency: "annual" }),
    ).toBe("9.5% floating interest, paid annual");
    expect(
      payoutStructureNote({ coupon_basis: "Zero Coupon", schedule_shape: "zero_coupon" }),
    ).toBe("No coupons · bought at a discount, single payment at maturity");
    expect(
      payoutStructureNote({ coupon: 8, frequency: "cumulative", schedule_shape: "cumulative" }),
    ).toBe("8% fixed interest · interest accrues and is paid with principal at maturity");
    expect(payoutStructureNote({})).toBeNull();
    expect(paymentsLeftText(21, 29)).toBe("21 of 29 payments still to come");
    expect(paymentsLeftText(0, 5)).toBe("no payments left to receive");
  });

  it("keeps an overnight snapshot until the bond's next payment or record date", () => {
    // Midnight rollover on an open tab: today flips 11 → 12 Sept; boundary is 18 Sept.
    for (const day of ["2026-09-12", "2026-09-17"]) {
      const kept = payoutDisplay(payout, day);
      expect(kept.label).toBe("₹12,560");
      expect(kept.showCount).toBe(true);
      expect(kept.state).toBe("known");
    }
    // No boundary known (pre-migration row or nothing ahead) → never stale.
    expect(payoutDisplay({ ...payout, payout_valid_until: null }, "2026-12-01").label).toBe(
      "₹12,560",
    );
    expect(payoutDisplay({ ...payout, payout_valid_until: undefined }, "2026-12-01").label).toBe(
      "₹12,560",
    );
    // Boundary is today and snapshot is today → compute already accounted for it.
    expect(payoutDisplay({ ...payout, payout_valid_until: "2026-09-11" }, "2026-09-11").label).toBe(
      "₹12,560",
    );
  });

  it("withholds the total and count once a boundary has passed since the snapshot", () => {
    for (const day of ["2026-09-18", "2026-09-25"]) {
      const stale = payoutDisplay(payout, day);
      expect(stale.label).toBeNull();
      expect(stale.showCount).toBe(false);
      expect(stale.state).toBe("stale");
      expect(stale.detail).toContain("07:00");
      expect(stale.detail).toContain("record date");
    }
    for (const state of ["incomplete", "open_ended", "unavailable", "stale"] as const) {
      expect(
        payoutDisplay({ ...payout, payout_total_state: state }, "2026-09-11").label,
      ).toBeNull();
    }
    expect(payoutDisplay({}, "2026-09-11").label).toBeNull();
    expect(payoutDisplay({}, "2026-09-11").showCount).toBe(true);
  });

  it("shows OTC turnover, yield and clean price on one calm line", () => {
    const b = {
      otc_yield: 12.26,
      otc_price_pct: 99.99,
      otc_trade_date: "2026-09-11",
      otc_turnover: 190000,
      turnover: 27550,
      prev_turnover: 21300,
      nse_turnover: 27550,
      bse_turnover: null,
    };
    const line = otcLine(b);
    expect(line?.text).toBe("OTC ₹1.90 L | 12.26% @ 99.99");
    expect(line).not.toHaveProperty("attention");
    expect(line?.title).toContain("Retail turnover ₹27,550 (prev ₹21,300)");
    expect(line?.title).toContain("OTC ₹1.90 L on 11 Sept 2026 at 99.99% of face, 12.26% yield");
    expect(line?.title).not.toMatch(/pts (above|below)/);
    // Each part is optional.
    expect(otcLine({ ...b, otc_price_pct: null })?.text).toBe("OTC ₹1.90 L | 12.26%");
    expect(otcLine({ ...b, otc_turnover: null })?.text).toBe("OTC 12.26% @ 99.99");
    expect(otcLine({ ...b, otc_yield: null, otc_price_pct: null })?.text).toBe("OTC ₹1.90 L");
    expect(otcLine({ ...b, otc_yield: null, otc_turnover: null })).toBeNull();
    expect(otcLine({ ...b, otc_turnover: 0 })?.text).toBe("OTC ₹0 | 12.26% @ 99.99");
  });

  it("formats the absolute return: one decimal, none at three digits, true minus when negative", () => {
    const at = (v: number | null, yield_type: "ytm" | "ytc" = "ytm") =>
      absReturnText({ holding_period_return: v, yield_type });
    expect(at(16.34)?.text).toBe("16.3%");
    expect(at(-12.4)?.text).toBe("\u221212.4%");
    expect(at(488.8)?.text).toBe("489%");
    expect(at(100)?.text).toBe("100%");
    expect(at(-41.04)?.text).toBe("\u221241.0%");
    expect(at(0)?.text).toBe("0.0%");
    expect(at(null)).toBeNull();
    expect(at(Number.NaN)).toBeNull();
    expect(absReturnText(null)).toBeNull();
    expect(at(16.34)?.title).toBe("Total return if held to maturity, before tax; not annualised.");
    expect(at(16.34, "ytc")?.title).toBe(
      "Total return if held to first call, before tax; not annualised.",
    );
    // Never a plus sign or a colour hint in the text itself.
    expect(at(16.34)?.text).not.toContain("+");
  });

  it("does not open the drawer when the click follows a text selection", () => {
    expect(shouldOpenRow("")).toBe(true);
    expect(shouldOpenRow(null)).toBe(true);
    expect(shouldOpenRow("   ")).toBe(true);
    expect(shouldOpenRow("INE104W07245")).toBe(false);
  });

  it("updates at IST midnight and on visibility/focus recovery, and cleans up", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-11T18:29:59Z"));
    const win = new EventTarget();
    const doc = Object.assign(new EventTarget(), { hidden: false });
    vi.stubGlobal("window", win);
    vi.stubGlobal("document", doc);
    const days: string[] = [];
    const stop = watchPayoutDay((day) => days.push(day));
    expect(days.at(-1)).toBe("2026-09-11");
    vi.advanceTimersByTime(1100);
    expect(days.at(-1)).toBe("2026-09-12");
    vi.setSystemTime(new Date("2026-09-13T06:00:00Z"));
    win.dispatchEvent(new Event("focus"));
    expect(days.at(-1)).toBe("2026-09-13");
    vi.setSystemTime(new Date("2026-09-14T06:00:00Z"));
    doc.dispatchEvent(new Event("visibilitychange"));
    expect(days.at(-1)).toBe("2026-09-14");
    stop();
    const count = days.length;
    win.dispatchEvent(new Event("focus"));
    vi.advanceTimersByTime(86400000);
    expect(days).toHaveLength(count);
    expect(vi.getTimerCount()).toBe(0);
  });
});
