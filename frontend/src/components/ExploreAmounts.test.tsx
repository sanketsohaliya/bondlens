import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { calendarAsOf } from "../lib/cashflowEvidence";
import { AskQuoteCell, PayoutCell } from "./ExploreTable";

const freshness = {
  as_of: calendarAsOf(undefined),
  calculation_version: "test",
  calculated_at: new Date().toISOString(),
  fetched_at: new Date().toISOString(),
};

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
  const text = (html: string) => html.replace(/<[^>]*>/g, "");

  it("uses pipes between payout fields without separating coupon and frequency", () => {
    const render = (overrides: Partial<typeof bond> = {}, today = "2026-09-11") =>
      text(
        renderToStaticMarkup(
          <PayoutCell bond={{ ...bond, ...overrides }} today={today} onOpen={() => {}} />,
        ),
      );
    expect(render()).toContain("14.4% monthly | 18/20 left");
    expect(render({ future_flows: 0 })).toContain("14.4% monthly | 0/20 left");
    expect(render({ coupon_basis: "Zero Coupon" })).toContain("Zero coupon | 18/20 left");
    expect(render({ total_flows: 0 })).not.toContain("|");
    const countOnly = text(
      renderToStaticMarkup(
        <PayoutCell bond={{ ...bond, coupon: null }} today="2026-09-11" onOpen={() => {}} />,
      ),
    );
    expect(countOnly).toContain("18/20 left");
    expect(countOnly).not.toContain("|");
    const stale = text(
      renderToStaticMarkup(
        <PayoutCell
          bond={{ ...bond, payout_valid_until: "2026-09-12" }}
          today="2026-09-12"
          onOpen={() => {}}
        />,
      ),
    );
    expect(stale).not.toContain("|");
  });

  it("separates an available fresh ask return from its ticket, never an absent return", () => {
    const ask = {
      ...freshness,
      isin: "TEST",
      ask_price: 10025,
      ask_qty: 10,
      ytm: 12.34,
      holding_period_return: 16.3,
    };
    const render = (value: typeof ask | null) =>
      text(renderToStaticMarkup(<AskQuoteCell ask={value} onOpen={() => {}} />));
    expect(render(ask)).toContain("16.3% | ₹10,025 (₹1.00 L)");
    expect(render({ ...ask, holding_period_return: 0 })).toContain("0.0% | ₹10,025 (₹1.00 L)");
    expect(render({ ...ask, holding_period_return: Number.NaN })).not.toContain("|");
    // An ask is a page-load snapshot: hours old, it still reads the same
    // in the row, with its capture time in the tooltip only.
    const aged = new Date(Date.now() - 3 * 3600_000).toISOString();
    const agedAsk = { ...ask, fetched_at: aged, calculated_at: aged };
    expect(render(agedAsk)).toContain("16.3% | ₹10,025 (₹1.00 L)");
    expect(render(agedAsk)).not.toContain("as of");
    expect(renderToStaticMarkup(<AskQuoteCell ask={agedAsk} onOpen={() => {}} />)).toMatch(
      /title="[^"]*Best ask as of \d\d:\d\d IST; captured when the page loaded/,
    );
    expect(render({ ...ask, ask_qty: 0 })).not.toContain("|");
    expect(render(null)).not.toContain("|");
  });

  it("keeps structure and qualified per-unit payout together above coupon and counts", () => {
    const html = renderToStaticMarkup(
      <PayoutCell bond={bond} today="2026-09-11" onOpen={() => {}} />,
    );
    expect(html).toContain("Staggered");
    expect(html).toContain("~₹12,560");
    expect(html).not.toContain("/u<");
    expect(html).toContain("per unit");
    expect(html).toContain("paid monthly");
    expect(html).toContain("principal repaid in parts");
    expect(html).toContain("click to see every payment");
    expect(html).not.toContain("Fixed Interest · monthly · staggered");
    expect(html).not.toContain("buyer-receivable");
    expect(html).not.toMatch(/Staggered<\/span><span[^>]*>\s*·/);
    expect(html).toContain("14.4");
    expect(html).toContain("monthly");
    expect(html).toContain("18/20 left");
    expect(html.match(/<div/g)).toHaveLength(2);
    expect(html).toContain('type="button"');
  });

  it("keeps an overnight snapshot visible until the bond's next boundary", () => {
    const html = renderToStaticMarkup(
      <PayoutCell
        bond={{ ...bond, payout_valid_until: "2026-09-18" }}
        today="2026-09-12"
        onOpen={() => {}}
      />,
    );
    expect(html).toContain("~₹12,560");
    expect(html).toContain("18/20 left");
  });

  it("withholds amount and count once a boundary has passed, showing structure only", () => {
    const html = renderToStaticMarkup(
      <PayoutCell
        bond={{ ...bond, payout_valid_until: "2026-09-12" }}
        today="2026-09-12"
        onOpen={() => {}}
      />,
    );
    expect(html).not.toContain("12,560");
    expect(html).not.toContain("18/20 left");
    expect(html).not.toContain("–");
    expect(html).toContain("Staggered");
    expect(html).toContain("monthly");
    expect(html).toContain("07:00 IST refresh");
  });

  it("renders no dangling dash when the payout amount is unavailable", () => {
    const html = renderToStaticMarkup(
      <PayoutCell
        bond={{
          ...bond,
          schedule_shape: "perpetual",
          payout_total_state: "open_ended",
          payout_total_per_unit: null,
        }}
        today="2026-09-11"
        onOpen={() => {}}
      />,
    );
    expect(html).toContain("Perpetual");
    expect(html).not.toContain("–");
    expect(html).toContain("no maturity");
  });

  it("shows ticket value below ask yield and retains exact arithmetic in details", () => {
    const html = renderToStaticMarkup(
      <AskQuoteCell
        ask={{ ...freshness, isin: "TEST", ask_price: 10025, ask_qty: 10, ytm: 12.34 }}
        onOpen={() => {}}
      />,
    );
    expect(html).toContain("12.34%");
    expect(html).toContain("₹10,025 (₹1.00 L)");
    expect(html).not.toContain("ticket<");
    expect(html).toContain("₹1,00,250");
    expect(html).toContain("Order Book");
    expect(text(html)).not.toContain("|");
    expect(html.match(/<div/g)).toHaveLength(2);
  });

  it("shows available size without a yield, and no fabricated size without quantity", () => {
    const html = renderToStaticMarkup(
      <AskQuoteCell
        ask={{ isin: "TEST", ask_price: 1005, ask_qty: 10, ytm: null }}
        onOpen={() => {}}
      />,
    );
    expect(html).toContain("₹1,005 (₹10,050)");
    const unknown = renderToStaticMarkup(
      <AskQuoteCell
        ask={{ isin: "TEST", ask_price: 1005, ask_qty: null, ytm: 12.34 }}
        onOpen={() => {}}
      />,
    );
    expect(unknown).toContain("₹1,005 (size unavailable)");
    expect(unknown).not.toContain("₹0");
  });
});
