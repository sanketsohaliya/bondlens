import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Bond, PortfolioPosition } from "../api/types";
import { HOLDINGS_COLS, PositionRow, positionBond } from "./Portfolio";

const client = new QueryClient();
const text = (html: string) => html.replace(/<[^>]+>/g, "");

const bond = {
  isin: "INE000A01001",
  issuer_name: "Example issuer",
  nse_symbol: "EXAMPLE",
  credit_rating: "CRISIL: AA/Stable",
  rating_rank: 3,
  ltp: 1010,
  prev_ltp: 1000,
  trade_date: "2026-10-28",
  days_since_traded: 1,
  ytm: 9.1,
  ytm_reliable: true,
  valuation: null,
  turnover: 250000,
  maturity_date: "2029-06-30",
  years_to_maturity: 2.7,
  tenor_months: 60,
  coupon: 9,
  frequency: "annual",
  schedule_shape: "bullet",
  face_value: 1000,
  in_default: false,
  stale: false,
} as unknown as Bond;

const position = {
  isin: "INE000A01001",
  symbol: "EXAMPLE",
  issuer_name: "Example issuer",
  avg_price: 990,
  credit_rating: "CRISIL: AA/Stable",
  rating_rank: 3,
  maturity_date: "2029-06-30",
  years_to_maturity: 2.7,
  face_value: 1000,
  ltp: 1010,
  ytm: 9.1,
  yield_on_cost: 9.6,
  units: 10,
  invested: 9900,
  value: 10100,
  gain: 200,
  gain_pct: 2,
  lots: [],
  next_payment: null,
  remaining: { amount: 12700, estimated: false, flows_left: 3, flows_total: 5 },
  bond,
} as unknown as PortfolioPosition;

const row = (p: PortfolioPosition) =>
  renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <table>
        <tbody>
          <PositionRow
            p={p}
            today="2026-10-29"
            totalValue={20200}
            open={false}
            onToggle={() => {}}
            onSelect={() => {}}
          />
        </tbody>
      </table>
    </QueryClientProvider>,
  );

describe("holdings table reads like Explore", () => {
  it("orders columns money first, then Explore's block, then bookkeeping", () => {
    expect(HOLDINGS_COLS.map((c) => c.label)).toEqual([
      "",
      "Symbol",
      "Value",
      "P&L",
      "Projected XIRR",
      "Rating",
      "Payout",
      "Maturity",
      "Invested",
      "Next payment",
    ]);
  });

  it("renders the Explore cells from the explorer row in that order", () => {
    const html = text(row(position));
    const order = [
      "Example issuer",
      "₹10,100",
      "50.0% of portfolio",
      "+₹200",
      "9.60%",
      "vs 9.10% YTM @ LTP",
      "AA/Stable",
      "₹12,700",
      "9% annual",
      "2y 8mo",
      "₹9,900",
      "10 × ₹990",
    ];
    const idx = order.map((s) => html.indexOf(s));
    expect(
      idx.every((i) => i >= 0),
      html,
    ).toBe(true);
    expect(idx).toEqual([...idx].sort((a, b) => a - b));
    expect(html).not.toContain("Retail");
    expect(html).not.toContain("₹1,270"); // Explore's per-unit payout is not shown
  });

  it("falls back to the position's own fields when the explorer row is missing", () => {
    const b = positionBond({ ...position, bond: null } as PortfolioPosition);
    expect(b.isin).toBe("INE000A01001");
    expect(b.nse_symbol).toBe("EXAMPLE");
    expect(text(row({ ...position, bond: null } as PortfolioPosition))).toContain("Example issuer");
  });
});
