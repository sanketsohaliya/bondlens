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
  payout_total_state: "known",
  payout_total_per_unit: 1270,
  payout_as_of: "2026-10-29",
  payout_valid_until: null,
  future_flows: 3,
  total_flows: 5,
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
  received: { interest: 150, principal: 0, payments: 2 },
  upcoming: [],
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
      "Projected XIRR",
      "Invested",
      "Value",
      "P&L",
      "Rating",
      "Payout",
      "Next payment",
      "Maturity",
    ]);
  });

  it("renders the Explore cells from the explorer row in that order", () => {
    const html = text(row(position));
    const order = [
      "Example issuer",
      "9.60%",
      "vs 9.10% YTM @ LTP",
      "₹9,900",
      "10 × ₹990 | ₹150 back",
      "₹10,100",
      "LTP ₹1,010",
      "+₹200",
      "AA/Stable",
      "9% annual",
      "₹1,270",
      "2y 8mo",
    ];
    const idx = order.map((s) => html.indexOf(s));
    expect(
      idx.every((i) => i >= 0),
      html,
    ).toBe(true);
    expect(idx).toEqual([...idx].sort((a, b) => a - b));
    expect(html).not.toContain("Retail");
    expect(html).not.toContain("₹12,700"); // per unit, as on Explore
  });

  it("always says what has come back, ₹0 for a fresh position", () => {
    expect(text(row({ ...position, received: null } as PortfolioPosition))).toContain(
      "10 × ₹990 | ₹0 back",
    );
  });

  it("shows the live bid under Value when Kite is connected", () => {
    const html = text(
      renderToStaticMarkup(
        <QueryClientProvider client={client}>
          <table>
            <tbody>
              <PositionRow
                p={position}
                today="2026-10-29"
                quote={{
                  exchange: "BSE",
                  bid_price: 1005,
                  bid_qty: 4,
                  bid_ytm: 9.3,
                  ask_price: null,
                  ask_qty: null,
                  ask_ytm: null,
                  quote_at: null,
                }}
                kiteConnected
                totalValue={20200}
                open={false}
                onToggle={() => {}}
                onSelect={() => {}}
              />
            </tbody>
          </table>
        </QueryClientProvider>,
      ),
    );
    expect(html).toContain("LTP ₹1,010 | bid ₹1,005 × 4");
    expect(text(row(position))).not.toContain("bid");
  });

  it("falls back to the position's own fields when the explorer row is missing", () => {
    const b = positionBond({ ...position, bond: null } as PortfolioPosition);
    expect(b.isin).toBe("INE000A01001");
    expect(b.nse_symbol).toBe("EXAMPLE");
    expect(text(row({ ...position, bond: null } as PortfolioPosition))).toContain("Example issuer");
  });
});
