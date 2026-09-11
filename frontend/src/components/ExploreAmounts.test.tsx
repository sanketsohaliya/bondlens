import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AskQuoteCell, PayoutCell } from "./ExploreTable";

const bond = {
  schedule_shape: "staggered",
  frequency: "monthly",
  coupon_basis: "Fixed Interest",
  coupon: 14.4,
  future_flows: 18,
  total_flows: 20,
  payout_total_per_unit: 12560,
  payout_total_state: "estimated" as const,
  payout_as_of: "2026-09-11",
};

describe("explore amount cells", () => {
  it("keeps structure and qualified per-unit payout together above coupon and counts", () => {
    const html = renderToStaticMarkup(
      <PayoutCell bond={bond} today="2026-09-11" onOpen={() => {}} />,
    );
    expect(html).toContain("Staggered");
    expect(html).toContain("~₹12,560/u");
    expect(html).toContain("14.4");
    expect(html).toContain("monthly");
    expect(html).toContain("18/20 left");
    expect(html.match(/<div/g)).toHaveLength(2);
    expect(html).toContain('type="button"');
  });

  it("withholds stale amounts and count fractions while preserving other payout information", () => {
    const html = renderToStaticMarkup(
      <PayoutCell bond={bond} today="2026-09-12" onOpen={() => {}} />,
    );
    expect(html).not.toContain("~₹12,560/u");
    expect(html).not.toContain("18/20 left");
    expect(html).toContain("Staggered");
    expect(html).toContain("monthly");
  });

  it("shows ticket value below ask yield and retains exact arithmetic in details", () => {
    const html = renderToStaticMarkup(
      <AskQuoteCell
        ask={{ isin: "TEST", ask_price: 10025, ask_qty: 10, ytm: 12.34 }}
        onOpen={() => {}}
      />,
    );
    expect(html).toContain("12.34%");
    expect(html).toContain("₹1.00 L ticket");
    expect(html).toContain("₹1,00,250");
    expect(html).toContain("Order Book");
    expect(html.match(/<div/g)).toHaveLength(2);
  });

  it("shows available size without a yield, and no fabricated size without quantity", () => {
    const html = renderToStaticMarkup(
      <AskQuoteCell
        ask={{ isin: "TEST", ask_price: 1005, ask_qty: 10, ytm: null }}
        onOpen={() => {}}
      />,
    );
    expect(html).toContain("₹10,050 ticket");
    const unknown = renderToStaticMarkup(
      <AskQuoteCell
        ask={{ isin: "TEST", ask_price: 1005, ask_qty: null, ytm: 12.34 }}
        onOpen={() => {}}
      />,
    );
    expect(unknown).toContain("Size unavailable");
    expect(unknown).not.toContain("₹0 ticket");
  });
});
