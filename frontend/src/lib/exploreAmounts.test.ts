import { afterEach, describe, expect, it, vi } from "vitest";
import type { AskYtm } from "../api/types";
import { askTicket, askTicketText, payoutDisplay, watchPayoutDay } from "./exploreAmounts";

const quote: AskYtm = { isin: "TEST", ask_price: 1005, ask_qty: 10, ytm: 12.34 };
const payout = {
  payout_total_per_unit: 12560,
  payout_total_state: "known" as const,
  payout_as_of: "2026-09-11",
};

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("explore amounts", () => {
  it("shows the entire selected best-level ticket, not one unit or a minimum lot", () => {
    expect(askTicket(quote)).toBe(10050);
    expect(askTicketText(quote).label).toBe("₹10,050 ticket");
    expect(askTicketText({ ...quote, ask_price: 10025 }).label).toBe("₹1.00 L ticket");
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
      "₹2.50 Cr ticket",
    );
  });

  it("labels current per-unit totals, estimates and known zero", () => {
    expect(payoutDisplay(payout, "2026-09-11").label).toBe("₹12,560/u");
    expect(payoutDisplay({ ...payout, payout_total_state: "estimated" }, "2026-09-11").label).toBe(
      "~₹12,560/u",
    );
    expect(payoutDisplay({ ...payout, payout_total_per_unit: 0 }, "2026-09-11").label).toBe("₹0/u");
  });

  it("does not show an old cached total or its future-count fraction", () => {
    const stale = payoutDisplay(payout, "2026-09-12");
    expect(stale.label).toBe("–");
    expect(stale.showCount).toBe(false);
    expect(stale.detail).toContain("snapshot");
    for (const state of ["incomplete", "open_ended", "unavailable", "stale"] as const) {
      expect(payoutDisplay({ ...payout, payout_total_state: state }, "2026-09-11").label).toBe("–");
    }
    expect(payoutDisplay({}, "2026-09-11").label).toBe("–");
    expect(payoutDisplay({}, "2026-09-11").showCount).toBe(true);
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
