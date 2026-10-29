import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { PortfolioPosition } from "../api/types";
import { LotEditor } from "./LotsPanel";
import PositionPanel, { ifSoldNow } from "./PositionPanel";

const client = new QueryClient();
const text = (html: string) => html.replace(/<[^>]+>/g, "");

const position = {
  isin: "INE000A01001",
  issuer_name: "Example issuer",
  avg_price: 1000,
  ltp: 1010,
  units: 2,
  invested: 2000,
  value: 2020,
  gain: 20,
  gain_pct: 1,
  lots: [
    {
      id: 1,
      isin: "INE000A01001",
      units: 1,
      price: 1050,
      purchase_date: "2026-01-05",
      platform: "Zerodha",
      value: 1010,
      gain: -40,
      gain_pct: -3.81,
      xirr: 7.9,
    },
    {
      id: 2,
      isin: "INE000A01001",
      units: 1,
      price: 950,
      purchase_date: "2026-03-10",
      platform: null,
      value: 1010,
      gain: 60,
      gain_pct: 6.32,
      xirr: 12.4,
    },
  ],
  next_payment: null,
  received: { interest: 90, principal: 0, payments: 1 },
  upcoming: [
    {
      event: "interest",
      due_date: "2026-11-28",
      record_date: "2026-10-13",
      payment_date: "2026-11-28",
      amount: 90,
      units: 2,
      estimated: false,
      locked: true,
    },
    {
      event: "interest",
      due_date: "2026-12-28",
      record_date: "2026-12-13",
      payment_date: "2026-12-28",
      amount: 90,
      units: 2,
      estimated: false,
      locked: false,
    },
  ],
} as unknown as PortfolioPosition;

const render = (quote: Parameters<typeof PositionPanel>[0]["quote"], connected: boolean) =>
  text(
    renderToStaticMarkup(
      <QueryClientProvider client={client}>
        <PositionPanel p={position} quote={quote} kiteConnected={connected} onSelect={() => {}} />
      </QueryClientProvider>,
    ),
  );

describe("position panel", () => {
  it("shows each lot's performance, the next payments with register status, and received so far", () => {
    const html = render(undefined, false);
    expect(html).toContain("Your lots2 lots");
    expect(html).toContain("05 Jan 2026");
    expect(html).not.toContain("₹1,010"); // no per-lot value column: same LTP for every lot
    expect(html).toContain("−₹40 (-3.8%)");
    expect(html).toContain("+₹60 (6.3%)");
    expect(html).toContain("12.40%");
    expect(html).toContain("₹2,000 invested | avg ₹1,000");
    expect(html).toContain("+ Add lot");
    expect(html).toContain("Cashflowsyour 2 units");
    expect(html).toContain("28 Nov 2026₹90on register ✓");
    expect(html).toContain("28 Dec 2026₹90hold → 13 Dec 2026");
    expect(html).toContain("Received so far ₹90 | 1 payment");
    expect(html).toContain("Connect Kite to see the live bid");
    expect(html).not.toContain("Sell at bid");
  });

  it("prices an exit against the live bid", () => {
    const quote = {
      exchange: "BSE",
      bid_price: 990,
      bid_qty: 1,
      bid_ytm: 11.2,
      bid_exchange: "BSE",
      ask_price: null,
      ask_qty: null,
      ask_ytm: null,
      quote_at: null,
    };
    const html = render(quote, true);
    expect(html).toContain("ExitBSE");
    expect(html).toContain("₹990 × 1 11.20%");
    expect(html).toContain("−₹10 (-1.0%) · bid covers 1 of 2 units");
    expect(html).toContain("Sell at bid");
    expect(ifSoldNow(position, quote)).toMatchObject({ covered: 1, proceeds: 990, gain: -10 });
    expect(render({ ...quote, bid_price: null, bid_qty: null }, true)).toContain(
      "no bid right now",
    );
  });

  it("lot form is labeled, defaults to today and offers known platforms", () => {
    const html = renderToStaticMarkup(
      <LotEditor
        platforms={["Zerodha", "IndiaBonds"]}
        pending={false}
        onSave={() => {}}
        onCancel={() => {}}
      />,
    );
    const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
    expect(html).toContain(`value="${today}"`);
    for (const label of ["Bought", "Units", "Paid ₹ / unit", "Platform", "Save lot", "Cancel"]) {
      expect(html).toContain(label);
    }
    expect(html).toContain('<option value="Zerodha">');
    expect(text(html)).toContain("= – invested");
    expect(
      text(
        renderToStaticMarkup(
          <LotEditor
            initial={position.lots[0]}
            pending={false}
            onSave={() => {}}
            onCancel={() => {}}
          />,
        ),
      ),
    ).toContain("Edit lot · bought 05 Jan 2026");
  });
});
