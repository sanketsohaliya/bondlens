import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PortfolioMonth, PortfolioNews, PortfolioPosition } from "../api/types";
import { MonthPopover, NewsList, PositionRow } from "../pages/Portfolio";
import BondDrawer from "./BondDrawer";
import LoginShowcase from "./LoginShowcase";
import OrderBook from "./OrderBook";
import OrderTicket from "./OrderTicket";
import RatingTimeline from "./RatingTimeline";

const text = (html: string) => html.replace(/<[^>]*>/g, "");
let client: QueryClient;
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-13T10:00:00Z"));
  vi.stubGlobal("window", {
    innerWidth: 1200,
    innerHeight: 800,
    matchMedia: () => ({ matches: true }),
  });
  vi.stubGlobal("localStorage", { getItem: () => null });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
});
afterEach(() => {
  client.clear();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
const render = (node: ReactNode) =>
  renderToStaticMarkup(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
const position: PortfolioPosition = {
  isin: "INE000A01001",
  symbol: "EXAMPLE",
  issuer_name: "Example issuer",
  avg_price: 1000,
  credit_rating: "INDIA RATINGS: AAA/Stable",
  rating_rank: null,
  rating_grade: null,
  rating_change: null,
  rating_prev: null,
  rating_changed_at: null,
  sector: null,
  maturity_date: "2033-11-01",
  years_to_maturity: 7.2,
  face_value: 1000,
  ltp: 1000,
  ytm: 8,
  yield_on_cost: 8,
  units: 1,
  invested: 1000,
  value: 1000,
  gain: 0,
  gain_pct: 0,
  lots: [],
  next_payment: null,
};
const month: PortfolioMonth = {
  month: "2026-08",
  interest: 90,
  principal: 1000,
  received_interest: 90,
  received_principal: 1000,
  estimated: true,
  bonds: [],
};
const rect = {
  x: 20,
  y: 300,
  left: 20,
  right: 40,
  top: 300,
  bottom: 320,
  width: 20,
  height: 20,
  toJSON: () => ({}),
};

describe("secondary metadata across screens", () => {
  it("separates holdings identifiers while leaving single-field sublines intact", () => {
    const row = (p: PortfolioPosition) =>
      text(
        render(
          <table>
            <tbody>
              <PositionRow
                p={p}
                totalValue={1000}
                open={false}
                onToggle={() => {}}
                onSelect={() => {}}
              />
            </tbody>
          </table>,
        ),
      );
    expect(row(position)).toContain("EXAMPLE | INE000A01001");
    expect(row(position)).toContain("AAA/StableINDIA RATINGS");
    expect(row({ ...position, symbol: null })).not.toContain("|");
  });

  it("uses pipes in month metadata and keeps the estimate qualifier attached", () => {
    const html = text(render(<MonthPopover m={month} year={2026} monthIdx={7} rect={rect} />));
    expect(html).toContain("Aug 2026 | ₹1,090 | past scheduled (≈ est.)");
    const future = text(
      render(
        <MonthPopover
          m={{ ...month, month: "2026-10", estimated: false }}
          year={2026}
          monthIdx={9}
          rect={rect}
        />,
      ),
    );
    expect(future).toContain("Oct 2026 | ₹1,090");
    expect(future).not.toContain("past scheduled");
    expect(future.match(/\|/g)).toHaveLength(1);
  });

  it("separates news dates from press-release links only when both exist", () => {
    const news: PortfolioNews = {
      isin: position.isin,
      issuer_name: "Example issuer",
      agency: "CARE",
      rating: "AAA",
      action: "Reaffirmed",
      rating_date: "2026-09-12",
      press_release: "https://example.test/pr",
    };
    const view = (n: PortfolioNews) => text(render(<NewsList news={[n]} onSelect={() => {}} />));
    expect(view(news)).toContain("12/9/2026 | PR ↗");
    expect(view({ ...news, rating_date: null })).not.toContain("|");
    expect(view({ ...news, press_release: null })).not.toContain("|");
  });

  it("separates drawer identifiers and optional same-issuer fields", () => {
    const detail = {
      isin: "TEST",
      issuer_name: "Example issuer",
      sector: "Finance",
      terms: {},
      schedule_shape: "plain",
      cashflows: [],
      schedule_warnings: [],
      call_dates: [],
      documents: [],
      issuer_bonds: [
        { isin: "OTHER", coupon_rate: 9.25, redemption_date: "2030-01-01", close: 1000 },
      ],
    };
    client.setQueryData(["portfolio"], { positions: [] });
    client.setQueryData(["bond", "TEST", undefined], detail);
    const drawer = () => render(<BondDrawer isin="TEST" onClose={() => {}} />);
    const html = drawer();
    expect(text(html.match(/<header[\s\S]*?<\/header>/)?.[0] ?? "")).toContain("TEST | Finance");
    expect(text(html)).toContain("9.25% | 01 Jan 2030 | ₹1,000");
    client.setQueryData(["bond", "TEST", undefined], {
      ...detail,
      sector: null,
      issuer_bonds: [{ ...detail.issuer_bonds[0], coupon_rate: null }],
    });
    const missing = drawer();
    expect(text(missing.match(/<header[\s\S]*?<\/header>/)?.[0] ?? "")).not.toContain("|");
    expect(text(missing)).toContain("OTHER01 Jan 2030 | ₹1,000");
  });

  it("separates snapshot metadata without changing the execution caveat", () => {
    const fetched_at = "2026-09-13T10:00:00Z";
    const book = {
      isin: "TEST",
      fetched_at,
      nse: null,
      ytm_at_ask: [],
      bse: {
        bids: [],
        asks: [],
        total_bid_qty: 0,
        total_ask_qty: 0,
        as_of: null,
        fetched_at,
      },
    };
    client.setQueryData(["kite-status"], { configured: false, connected: false, login_url: null });
    client.setQueryData(["depth", "TEST"], book);
    const view = () => text(render(<OrderBook isin="TEST" />));
    expect(view()).toContain("IST | not a guaranteed executable offer");
    expect(view()).toContain("Snapshot | 15:30:00");
    expect(view()).not.toContain("Expired");
    // Kite's timestamp is when the book last changed: an old one is
    // disclosed as such, never as an expired quote.
    client.setQueryData(["depth", "TEST"], {
      ...book,
      bse: { ...book.bse, quote_at: "2026-09-13T03:45:27Z" },
    });
    expect(view()).toContain("Snapshot | 15:30:00 | book unchanged since 09:15");
    client.setQueryData(["depth", "TEST"], {
      ...book,
      bse: { ...book.bse, fetched_at: null },
    });
    expect(view()).toContain("Snapshot | time unknown");
  });

  it("changes the muted order-type summary but not the primary ticket title", () => {
    const html = text(
      render(
        <OrderTicket
          isin="TEST"
          exchange="BSE"
          initialSide="SELL"
          initialPrice={1000}
          ytmAtAsk={[]}
          onClose={() => {}}
        />,
      ),
    );
    expect(html).toContain("Order typeLIMIT | CNC | DAY");
    expect(html).toContain("SELL · TEST · BSE");
  });

  it("keeps rating/outlook intact while separating action, date and source", () => {
    const event = {
      rating: "AAA",
      outlook: "Stable",
      action: "Rating reaffirmed",
      rating_date: "2026-09-12",
      press_release: "https://example.test/pr",
    };
    const view = (
      e:
        | typeof event
        | {
            rating: string;
            outlook: string;
            action: null;
            rating_date: string;
            press_release: null;
          },
    ) => text(render(<RatingTimeline timeline={{ CARE: [e] }} />));
    expect(view(event)).toContain("AAA/StableRating reaffirmed | 12 Sept 2026 | press release ↗");
    expect(view({ ...event, action: null, press_release: null })).not.toContain("|");
  });

  it("uses the same separator in the decorative exchange/session metadata", () => {
    expect(text(render(<LoginShowcase />))).toContain("NSE | BSE | live session");
  });
});
