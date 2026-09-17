import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Bond } from "../api/types";
import AiPromptBtn from "./AiPromptBtn";
import { Row, RowLink } from "./ExploreTable";

const client = new QueryClient();
const wrap = (node: React.ReactElement) =>
  renderToStaticMarkup(<QueryClientProvider client={client}>{node}</QueryClientProvider>);

const bond: Bond = {
  isin: "INE000A01001",
  issuer_name: "Example issuer",
  sector: null,
  industry: null,
  on_nse: true,
  on_bse: false,
  nse_symbol: "EXAMPLE",
  nse_series: null,
  bse_scrip_id: null,
  coupon: null,
  face_value: 1000,
  coupon_basis: null,
  frequency: null,
  secured: true,
  tax_free: null,
  perpetual: false,
  maturity_date: "2033-11-01",
  years_to_maturity: 7.2,
  schedule_shape: "plain",
  ltp: 1265,
  trade_date: null,
  exchange: null,
  volume: null,
  turnover: null,
  num_trades: null,
  nse_turnover: null,
  bse_turnover: null,
  prev_turnover: null,
  prev_ltp: null,
  days_since_traded: 2,
  stale: false,
  otc_price_pct: null,
  otc_yield: null,
  otc_trade_date: null,
  otc_days_since_traded: null,
  otc_turnover: null,
  ytm: 5.08,
  ytm_method: null,
  ytm_reliable: true,
  valuation: { holding_period_return: 32.6 },
  current_yield: null,
  duration: null,
  credit_rating: "INDIA RATINGS: AAA/Stable",
  rating_rank: null,
  rating_change: null,
  rating_prev: null,
  rating_changed_at: null,
  in_default: false,
  tech_delay: false,
  computed_at: "2026-09-13T00:00:00Z",
};

const renderRow = (overrides: Partial<Bond> = {}) => {
  const queryClient = new QueryClient();
  try {
    return renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <table>
          <tbody>
            <Row bond={{ ...bond, ...overrides }} today="2026-09-13" onSelect={() => {}} />
          </tbody>
        </table>
      </QueryClientProvider>,
    );
  } finally {
    queryClient.clear();
  }
};
const text = (html: string) => html.replace(/<[^>]*>/g, "");
const cell = (html: string, index: number) =>
  [...html.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)][index][1];

describe("explore row actions", () => {
  it("separates identifiers and each hover action without changing copy targets", () => {
    const html = cell(renderRow({ kite_url: "https://example.test/kite" }), 1);
    expect(text(html)).toContain("EXAMPLE | INE000A01001 | NSDL ↗ | Kite ↗ | Ask AI ↗");
    expect(html).toMatch(/aria-label="Copy symbol EXAMPLE"[^>]*>EXAMPLE<\/button>/);
    const hiddenGroup = html.slice(
      html.indexOf('<span class="flex items-center shrink-0 opacity-0'),
    );
    expect(hiddenGroup).toMatch(/opacity-0[^>]*><span[^>]*> \| <\/span>/);
    expect(text(cell(renderRow(), 1))).toContain("EXAMPLE | INE000A01001 | NSDL ↗ | Ask AI ↗");
  });

  it("separates only actually visible LTP fields and preserves single-field metadata", () => {
    // Trade recency rides with the price it qualifies; no Last traded column.
    expect(text(cell(renderRow(), 2))).toBe("5.08%32.6% | ₹1,265 | 2d ago");
    expect(text(cell(renderRow({ days_since_traded: 1 }), 2))).toBe("5.08%32.6% | ₹1,265 | today");
    expect(text(cell(renderRow({ valuation: { holding_period_return: 0 } }), 2))).toContain(
      "0.0% | ₹1,265 | 2d ago",
    );
    for (const valuation of [null, {}, { holding_period_return: Number.NaN }]) {
      expect(text(cell(renderRow({ valuation }), 2))).toBe("5.08%₹1,265 | 2d ago");
    }
    expect(text(cell(renderRow({ ltp: null }), 2))).toBe("5.08%32.6%");
    expect(text(cell(renderRow({ ltp: null, days_since_traded: null }), 2))).toBe("5.08%32.6%");
    expect(text(cell(renderRow({ ytm: null, valuation: null, ltp: null }), 2))).not.toContain("|");
    expect(text(cell(renderRow(), 4))).toBe("AAA/StableINDIA RATINGS (S)");
    expect(text(cell(renderRow(), 6))).not.toContain("|");
    expect([...renderRow().matchAll(/<td[^>]*>/g)]).toHaveLength(7);
    expect(renderRow()).not.toContain("never");
  });

  it("does not put a warning glyph beside a stale price; the hover still explains the age", () => {
    const stale = { ...bond, stale: true, days_since_traded: 27, prev_ltp: null };
    const html = wrap(
      <table>
        <tbody>
          <Row bond={stale} today="2026-09-13" onSelect={() => {}} ask={undefined} />
        </tbody>
      </table>,
    );
    const yieldCell = html.split("<td")[3] ?? "";
    // The badge beside the yield is severity-driven; a week-old price is not
    // a doubt about the number, so no \u26a0 anywhere in this cell.
    expect(yieldCell).not.toContain("\u26a0");
    expect(yieldCell).toContain("No trades seen for 27 days");
    expect(text(yieldCell)).toContain("| 27d ago");
  });

  it("renders an external link that opens safely in a new tab and explains itself", () => {
    const html = renderToStaticMarkup(
      <RowLink href="https://example.test/x" label="NSDL" tip="Official bond record on NSDL" />,
    );
    expect(html).toContain('href="https://example.test/x"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).toContain('title="Official bond record on NSDL"');
    expect(html).toContain('aria-label="Official bond record on NSDL"');
    expect(html).toContain("NSDL ↗");
  });

  it("labels the AI action as a verb with an explanatory tooltip in both variants", () => {
    for (const variant of ["row", "drawer"] as const) {
      const html = wrap(<AiPromptBtn isin="INE000A01001" variant={variant} />);
      expect(html).toContain("Ask AI ↗");
      expect(html).toContain("Opens ChatGPT with this bond");
      expect(html).toContain('type="button"');
      expect(html).not.toContain("Download");
      expect(html).not.toContain("Open ChatGPT");
    }
  });

  it("uses the compact text style for the row and the pill for the drawer", () => {
    expect(wrap(<AiPromptBtn isin="X" variant="row" />)).toContain("text-[10px]");
    expect(wrap(<AiPromptBtn isin="X" variant="drawer" />)).toContain("rounded-md border");
  });
});
