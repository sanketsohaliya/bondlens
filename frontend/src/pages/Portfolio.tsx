import { useQuery } from "@tanstack/react-query";
import {
  createChart,
  HistogramSeries,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, fmt } from "../api/client";
import type {
  Bond,
  HoldingQuote,
  PortfolioMonth,
  PortfolioNews,
  PortfolioPosition,
} from "../api/types";
import AddLotSearch from "../components/AddLotSearch";
import { Switch } from "../components/AlertsPanel";
import type { Tab as DrawerTab } from "../components/BondDrawer";
import CopyBtn from "../components/CopyBtn";
import Donut from "../components/Donut";
import {
  BookmarkIcon,
  MaturityCell,
  PayoutTd,
  RatingCell,
  SymbolCell,
  tradedAgo,
  WatchStar,
} from "../components/ExploreTable";
import InlineMetadata, { MetadataSeparator } from "../components/InlineMetadata";
import type { TicketState } from "../components/OrderBook";
import PositionPanel from "../components/PositionPanel";
import StatBox from "../components/StatBox";
import { usePayoutDay } from "../lib/exploreAmounts";
import { fuzzyMatch } from "../lib/fuzzy";
import {
  allDatesPast,
  cashAmount,
  isPastMonth,
  monthGrid,
  netInterest,
  portfolioYieldSpread,
  TDS_RATE,
  tds,
  usePortfolio,
  type WeightBy,
  weights,
} from "../lib/portfolio";
import { useColumnWidths } from "../lib/useColumnWidths";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const monthYear = (d: string | null) =>
  d ? new Date(d).toLocaleDateString("en-IN", { month: "short", year: "numeric" }) : "–";

/** "2028-03-17" → "17 Mar" — exact payment days inside a month bucket. */
const dayMonth = (dates: string[]) =>
  dates
    .map((d) => new Date(d).toLocaleDateString("en-IN", { day: "numeric", month: "short" }))
    .join(", ");

interface SliceFilter {
  by: WeightBy;
  label: string;
  isins: string[];
}

type PortfolioTab = "holdings" | "cashflow" | "breakdown" | "news";

const PORTFOLIO_TABS: Array<[PortfolioTab, string]> = [
  ["holdings", "Holdings"],
  ["cashflow", "Cashflow to maturity"],
  ["breakdown", "Investment breakdown"],
  ["news", "Market news"],
];

export default function Portfolio({
  onSelect,
}: {
  onSelect: (isin: string, tab?: DrawerTab, ticket?: TicketState) => void;
}) {
  const { data, isLoading, error } = usePortfolio();
  const [filter, setFilter] = useState<SliceFilter | null>(null);
  const [nameQuery, setNameQuery] = useState("");
  const [tab, setTab] = useState<PortfolioTab>("holdings");
  // Cash figures net of 10% TDS on interest (grid, chart, cards). Yields
  // and XIRR stay pre-tax, as quoted everywhere else.
  const [netTds, setNetTds] = useState(() => localStorage.getItem("portfolio-net-tds") === "1");
  useEffect(() => {
    localStorage.setItem("portfolio-net-tds", netTds ? "1" : "0");
  }, [netTds]);

  const bySlice = useMemo(() => {
    if (!data) return { issuer: [], rating: [], sector: [] };
    return {
      issuer: weights(data.positions, "issuer"),
      rating: weights(data.positions, "rating"),
      sector: weights(data.positions, "sector"),
    };
  }, [data]);

  if (isLoading) {
    return <div className="py-24 text-center text-sm text-zinc-500">Loading portfolio…</div>;
  }
  if (error) {
    return <div className="py-24 text-center text-sm text-red-400">{error.message}</div>;
  }
  if (!data || data.positions.length === 0) {
    return (
      <div className="py-24 text-center space-y-2">
        <div className="text-lg font-semibold text-zinc-300">Your portfolio is empty</div>
        <p className="text-sm text-zinc-500">
          Click the bookmark{" "}
          <span className="inline-block align-text-bottom text-zinc-300">
            <BookmarkIcon filled={false} />
          </span>{" "}
          next to any bond in Explore to record a purchase — units, price paid, date and platform.
        </p>
      </div>
    );
  }

  const { summary, positions, cashflows, income, news } = data;
  const sliced = filter ? positions.filter((p) => filter.isins.includes(p.isin)) : positions;
  const q = nameQuery.trim();
  const shown = q
    ? sliced.filter((p) => fuzzyMatch(q, p.issuer_name ?? "") || fuzzyMatch(q, p.isin))
    : sliced;

  const pickSlice = (by: WeightBy) => (label: string | null) => {
    if (label === null) {
      setFilter(null);
      return;
    }
    const slice = bySlice[by].find((s) => s.label === label);
    setFilter(slice ? { by, label, isins: slice.isins } : null);
    if (slice) setTab("holdings"); // show the freshly filtered table
  };

  return (
    <div className="space-y-6">
      {/* Each row reads left to right as the money's journey. Row 1: what
          went in and where it stands now. Row 2: what comes back out and
          when. Received / still-to-come share the cashflow chart's month
          buckets, so the two views cannot disagree. */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <StatBox label="Total invested" value={fmt.inr(summary.invested)} />
        <StatBox label="Current value" value={fmt.inr(summary.value)} accent />
        <GainBox gain={summary.gain} invested={summary.invested} />
        <StatBox
          label="Portfolio YTM"
          value={fmt.pct(summary.weighted_ytm)}
          tip="Value-weighted average of each holding's YTM @ LTP."
        />
        <StatBox
          label={netTds ? "Received so far (net of TDS)" : "Received so far"}
          value={fmt.inr(
            cashAmount(
              { interest: income.received_interest, principal: income.received_principal },
              netTds,
            ),
          )}
          tip={`Interest ${fmt.inr(income.received_interest)}${netTds ? ` less ${TDS_RATE * 100}% TDS ${fmt.inr(tds(income.received_interest))}` : ""} · Principal ${fmt.inr(income.received_principal)}. Per schedule to date; not confirmation of bank receipt. Optional exits excluded.`}
        />
        <StatBox
          label={netTds ? "Still to come (net of TDS)" : "Still to come"}
          value={fmt.inr(
            cashAmount(
              { interest: income.projected_interest, principal: income.projected_principal },
              netTds,
            ),
          )}
          accent
          tip={`Interest ${fmt.inr(income.projected_interest)}${netTds ? ` less ${TDS_RATE * 100}% TDS ${fmt.inr(tds(income.projected_interest))}` : ""} · Principal ${fmt.inr(income.projected_principal)}. Scheduled to maturity; optional exits excluded.`}
        />
        <StatBox
          label="Avg maturity"
          value={fmt.yearsMonths(summary.weighted_years)}
          tip="Value-weighted average time to maturity across holdings."
        />
        <StatBox
          label="Diversification"
          value={`${summary.issuers} issuer${summary.issuers === 1 ? "" : "s"} · ${summary.bonds} bond${summary.bonds === 1 ? "" : "s"}`}
        />
      </div>

      <section>
        <div className="flex items-center gap-1 border-b border-zinc-800">
          {PORTFOLIO_TABS.map(([t, label]) => (
            <button
              type="button"
              key={t}
              onClick={() => setTab(t)}
              className={
                "text-sm font-medium px-3.5 py-2 rounded-t-md border-b-2 transition-colors -mb-px " +
                (tab === t
                  ? "border-emerald-500 text-emerald-400"
                  : "border-transparent text-zinc-500 hover:text-zinc-300")
              }
            >
              {label}
            </button>
          ))}
          <span className="flex-1" />
          {tab === "cashflow" && (
            <span className="flex items-center gap-2 pb-1.5 text-[11px] text-zinc-400">
              Net of TDS
              <Switch
                on={netTds}
                onChange={setNetTds}
                title={`Show cash after ${TDS_RATE * 100}% TDS on interest (Section 193, listed debentures, PAN on record). Principal is not taxed at source.`}
              />
            </span>
          )}
          {tab === "holdings" && (
            <span className="flex items-center gap-2 pb-1.5">
              {filter && (
                <button
                  type="button"
                  onClick={() => setFilter(null)}
                  className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md border border-zinc-600 bg-zinc-800/60 text-[11px] text-zinc-200 hover:border-zinc-400"
                >
                  {filter.by}: {filter.label}
                  <span className="text-zinc-500">×</span>
                </button>
              )}
              <input
                value={nameQuery}
                onChange={(e) => setNameQuery(e.target.value)}
                placeholder="Filter by name / ISIN"
                className="w-52 bg-transparent border border-zinc-700 rounded-md px-2 py-1 text-[11px] font-medium focus:outline-none focus:border-zinc-400 placeholder:text-zinc-600"
              />
              <AddLotSearch />
            </span>
          )}
        </div>

        {/* Flush tab panel: the tab bar's border-b is the top edge, this shell
            provides the sides and rounded bottom. */}
        <div className="rounded-b-lg border border-t-0 border-zinc-800 overflow-hidden">
          {tab === "holdings" && <HoldingsTable positions={shown} onSelect={onSelect} />}

          {tab === "cashflow" &&
            (cashflows.length > 0 ? (
              <>
                <CashflowChart cashflows={cashflows} net={netTds} onSelect={onSelect} />
                <CashflowGrid cashflows={cashflows} net={netTds} onSelect={onSelect} />
              </>
            ) : (
              <div className="py-12 text-center text-sm text-zinc-600">No cashflows</div>
            ))}

          {tab === "breakdown" && (
            <div className="p-3">
              <div className="grid md:grid-cols-3 gap-3">
                <Donut
                  title="By issuer"
                  slices={bySlice.issuer}
                  selected={filter?.by === "issuer" ? filter.label : null}
                  onSlice={pickSlice("issuer")}
                />
                <Donut
                  title="By rating"
                  slices={bySlice.rating}
                  selected={filter?.by === "rating" ? filter.label : null}
                  onSlice={pickSlice("rating")}
                />
                <Donut
                  title="By sector"
                  slices={bySlice.sector}
                  selected={filter?.by === "sector" ? filter.label : null}
                  onSlice={pickSlice("sector")}
                />
              </div>
              <p className="mt-1.5 text-[10px] text-zinc-600">
                Weighted by current value. Click a slice to filter the holdings table.
              </p>
            </div>
          )}

          {tab === "news" &&
            (news.length > 0 ? (
              <NewsList news={news} onSelect={onSelect} />
            ) : (
              <div className="py-12 text-center text-sm text-zinc-600">
                No rating actions on your issuers
              </div>
            ))}
        </div>
      </section>
    </div>
  );
}

function GainBox({ gain, invested }: { gain: number | null; invested: number }) {
  if (gain == null) return <StatBox label="Unrealized P&L" value="–" />;
  const pct = invested ? ` (${((gain / invested) * 100).toFixed(2)}%)` : "";
  const sign = gain >= 0 ? "+" : "−";
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 px-3 py-2 h-full">
      <div className="text-[10px] text-zinc-500">Unrealized P&L</div>
      <div
        className={`text-xs font-semibold mt-0.5 ${gain >= 0 ? "text-emerald-400" : "text-red-400"}`}
      >
        {sign}
        {fmt.inr(Math.abs(gain)).replace("₹", "₹")}
        {pct}
      </div>
    </div>
  );
}

// --- Holdings table ---------------------------------------------------------

const TH = "px-3 py-2 font-medium whitespace-nowrap";
const TD = "px-3 py-2 whitespace-nowrap";

type HoldingsSortKey =
  | "issuer"
  | "value"
  | "gain"
  | "yield_on_cost"
  | "rating"
  | "remaining"
  | "maturity"
  | "invested"
  | "next_payment";

/** The explorer row for a position (the API attaches it so both pages
 * render a bond from the same data); a thin fallback keeps the shared
 * cells rendering when the analytics row is missing. */
export function positionBond(p: PortfolioPosition): Bond {
  if (p.bond) return p.bond as unknown as Bond;
  return {
    isin: p.isin,
    issuer_name: p.issuer_name,
    sector: p.sector,
    nse_symbol: p.symbol,
    credit_rating: p.credit_rating,
    rating_rank: p.rating_rank,
    rating_grade: p.rating_grade,
    rating_change: p.rating_change,
    rating_prev: p.rating_prev,
    rating_changed_at: p.rating_changed_at,
    maturity_date: p.maturity_date,
    years_to_maturity: p.years_to_maturity,
    face_value: p.face_value,
    ltp: p.ltp,
    ytm: p.ytm,
    ytm_reliable: true,
    in_default: false,
    stale: false,
  } as unknown as Bond;
}

/** Sort accessors — semantic, not display, values: rating by rank (lower =
 * better), maturity by remaining tenor, next payment by due date. */
const HOLDINGS_ACCESSORS: Record<
  HoldingsSortKey,
  (p: PortfolioPosition) => number | string | null | undefined
> = {
  issuer: (p) => (p.issuer_name ?? p.isin).toLowerCase(),
  value: (p) => p.value,
  gain: (p) => p.gain,
  yield_on_cost: (p) => p.yield_on_cost,
  rating: (p) => p.rating_rank,
  remaining: (p) => positionBond(p).payout_total_per_unit,
  maturity: (p) => p.years_to_maturity,
  invested: (p) => p.invested,
  next_payment: (p) => p.next_payment?.payment_date ?? p.next_payment?.due_date,
};

const INVESTED_TIP =
  "Units × units-weighted price paid. 'back' = cash received so far per the schedule (dated lots on the register by each record date) — not confirmation of bank receipt.";

const PAYOUT_TIP =
  "Per unit, as on Explore: what one unit bought today pays to maturity, and how many payments remain.";

const XIRR_TIP =
  "Return locked in on what you actually paid, across your dated purchases (XIRR), assuming scheduled cashflows to maturity. Compare with YTM @ LTP — what a buyer today would get.";

type HoldingsCol = {
  label: string;
  key: HoldingsSortKey | null;
  right?: boolean;
  tip?: string;
};

/** Reads left to right: what the position is doing (money), then the
 * bond as Explore shows it (Rating, Payout, Maturity - the same cells in
 * the same order), then the bookkeeping. Market-side columns (YTM @ LTP,
 * YTM @ Ask, turnover) are a buyer's concern; the market YTM survives as
 * the line under Projected XIRR. */
export const HOLDINGS_COLS: HoldingsCol[] = [
  { label: "", key: null }, // expand chevron + watch star
  { label: "Symbol", key: "issuer" },
  { label: "Projected XIRR", key: "yield_on_cost", right: true, tip: XIRR_TIP },
  { label: "Invested", key: "invested", right: true, tip: INVESTED_TIP },
  { label: "Value", key: "value", right: true },
  { label: "P&L", key: "gain", right: true },
  { label: "Rating", key: "rating" },
  { label: "Payout", key: "remaining", tip: PAYOUT_TIP },
  { label: "Next payment", key: "next_payment" },
  { label: "Maturity", key: "maturity", right: true },
];

function HoldingsTable({
  positions,
  onSelect,
}: {
  positions: PortfolioPosition[];
  onSelect: (isin: string, tab?: DrawerTab, ticket?: TicketState) => void;
}) {
  const [openIsin, setOpenIsin] = useState<string | null>(null);
  const [sort, setSort] = useState<{ key: HoldingsSortKey; dir: 1 | -1 }>({
    key: "value",
    dir: -1, // matches the backend's default biggest-position-first order
  });
  const today = usePayoutDay();
  const cols = HOLDINGS_COLS;
  // Live best bid / ask for every held bond: one batched Kite quote under
  // the user's own session, refreshed each minute while the page is open.
  const { data: kite } = useQuery({
    queryKey: ["kite-status"],
    queryFn: api.kite.status,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const kiteConnected = kite?.connected ?? false;
  const { data: quotes } = useQuery({
    queryKey: ["portfolio-quotes"],
    queryFn: api.portfolio.quotes,
    enabled: kiteConnected,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  const { widths, tableRef, startResize, tableProps } = useColumnWidths(
    "holdings-col-widths-v5",
    cols.length,
  );
  const totalValue = positions.reduce((s, p) => s + (p.value ?? 0), 0);
  const platforms = useMemo(
    () =>
      [
        ...new Set(positions.flatMap((p) => p.lots.map((l) => l.platform)).filter(Boolean)),
      ].sort() as string[],
    [positions],
  );

  const sorted = useMemo(() => {
    const get = HOLDINGS_ACCESSORS[sort.key];
    const rows = [...positions];
    rows.sort((a, b) => {
      const av = get(a);
      const bv = get(b);
      if (av == null) return 1; // nulls last in both directions
      if (bv == null) return -1;
      return (av < bv ? -1 : av > bv ? 1 : 0) * sort.dir;
    });
    return rows;
  }, [positions, sort]);

  return (
    <div className="overflow-x-auto">
      <table ref={tableRef} className={`text-xs ${tableProps.className}`} style={tableProps.style}>
        {widths && (
          <colgroup>
            {widths.map((w, i) => (
              <col key={i} style={{ width: w }} />
            ))}
          </colgroup>
        )}
        <thead>
          <tr className="text-left text-zinc-500 border-b border-zinc-800">
            {cols.map((c, i) => (
              <th
                key={c.label || i}
                title={c.tip}
                className={`relative ${TH} ${c.right ? "text-right" : ""} ${
                  c.key ? "cursor-pointer select-none hover:text-zinc-300" : ""
                }`}
                onClick={() =>
                  c.key &&
                  setSort((s) => ({
                    key: c.key as HoldingsSortKey,
                    dir: s.key === c.key ? ((s.dir * -1) as 1 | -1) : -1,
                  }))
                }
              >
                <span className={c.key === sort.key ? "text-zinc-200" : undefined}>
                  {c.label}
                  {c.tip && <span className="ml-1 text-zinc-600">ⓘ</span>}
                </span>
                {c.key === sort.key && (
                  <span className="ml-1 text-emerald-500/80">{sort.dir === -1 ? "▼" : "▲"}</span>
                )}
                {i > 0 && (
                  <span
                    className="absolute right-0 top-1/2 -translate-y-1/2 h-4 w-1.5 cursor-col-resize border-r-2 border-zinc-800 hover:border-emerald-500"
                    onPointerDown={(e) => startResize(i, e)}
                    onClick={(e) => e.stopPropagation()}
                    title="Drag to resize"
                  />
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((p) => (
            <PositionRow
              key={p.isin}
              p={p}
              today={today}
              quote={kiteConnected ? (quotes?.quotes[p.isin] ?? null) : undefined}
              kiteConnected={kiteConnected}
              platforms={platforms}
              totalValue={totalValue}
              open={openIsin === p.isin}
              onToggle={() => setOpenIsin(openIsin === p.isin ? null : p.isin)}
              onSelect={(tab?: DrawerTab, ticket?: TicketState) => onSelect(p.isin, tab, ticket)}
            />
          ))}
          {sorted.length === 0 && (
            <tr>
              <td colSpan={cols.length} className="px-3 py-6 text-center text-zinc-600">
                No holdings match
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function PositionRow({
  p,
  today,
  quote,
  kiteConnected = false,
  platforms,
  totalValue,
  open,
  onToggle,
  onSelect,
}: {
  p: PortfolioPosition;
  today?: string;
  /** undefined = Kite not connected; null = no live book for this bond. */
  quote?: HoldingQuote | null;
  kiteConnected?: boolean;
  platforms?: string[];
  totalValue: number;
  open: boolean;
  onToggle: () => void;
  onSelect: (tab?: DrawerTab, ticket?: TicketState) => void;
}) {
  const bond = positionBond(p);
  const payoutDay = today ?? new Date().toISOString().slice(0, 10);
  // Shared Explore cells take (isin, tab); this row already knows its ISIN.
  const openTab = (_isin: string, tab?: "cashflows" | "ratings" | "chart" | "order book") =>
    onSelect(tab);
  // Holding age from the oldest lot (context for the yield-on-cost XIRR).
  const oldest = p.lots.reduce<string | null>(
    (min, l) => (min == null || l.purchase_date < min ? l.purchase_date : min),
    null,
  );
  const heldDays = oldest
    ? Math.floor((Date.now() - new Date(oldest).getTime()) / 86_400_000)
    : null;
  const heldLabel =
    heldDays == null
      ? null
      : heldDays < 1
        ? "today"
        : heldDays < 31
          ? `over ${heldDays}d`
          : `over ${fmt.yearsMonths(heldDays / 365.25)}`;
  const weightPct = totalValue > 0 && p.value != null ? (p.value / totalValue) * 100 : null;
  const received = (p.received?.interest ?? 0) + (p.received?.principal ?? 0);
  const investedTitle =
    received > 0 && p.received
      ? `Units × units-weighted price paid. Received so far ${fmt.inr(received)} (interest ${fmt.inr(p.received.interest)} · principal ${fmt.inr(p.received.principal)}) across ${p.received.payments} payment${p.received.payments === 1 ? "" : "s"} per schedule to date — not confirmation of bank receipt; optional exits excluded.`
      : "Units × units-weighted price paid. Nothing received yet per the schedule.";
  // Compare effective annual rates, not nominal YTM against XIRR.
  // Different dates and cashflows mean this is not an entry recommendation.
  const spread = portfolioYieldSpread(p);
  return (
    <>
      <tr
        onClick={onToggle}
        aria-expanded={open}
        title={open ? "Click row to hide lots" : "Click row to show lots"}
        className="group/row border-b border-zinc-800/60 hover:bg-zinc-900/60 cursor-pointer"
      >
        <td className="w-14 pl-3 pr-1">
          <span className="flex items-center gap-1.5">
            <span
              className={`inline-block text-zinc-500 transition-transform duration-150 ${
                open ? "rotate-90 text-emerald-400" : ""
              }`}
              aria-hidden="true"
            >
              ›
            </span>
            <WatchStar isin={p.isin} />
          </span>
        </td>
        <SymbolCell bond={bond} onOpen={() => onSelect()} />
        {/* Projected XIRR over dated purchases and scheduled cashflows;
            optional exit payouts are not assumed to have been received. */}
        <td
          className={`${TD} text-right tabular-nums`}
          title={p.yield_on_cost_notes?.join("; ") || XIRR_TIP}
        >
          <div>{fmt.pct(p.yield_on_cost)}</div>
          {bond.ytm != null ? (
            <div
              className="text-[10px] text-zinc-500"
              title={[
                bond.ltp != null ? `LTP ${fmt.inrFull(bond.ltp)}` : null,
                bond.days_since_traded != null ? tradedAgo(bond.days_since_traded) : null,
                spread != null && Math.abs(spread) > 0.05
                  ? `${spread > 0 ? "+" : "−"}${Math.abs(spread).toFixed(2)} pp effective vs your XIRR`
                  : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            >
              vs {fmt.pct(bond.ytm)} YTM @ LTP
            </div>
          ) : bond.ltp != null ? (
            <div className="text-[10px] text-zinc-500">
              LTP {fmt.inrFull(bond.ltp)}
              {bond.days_since_traded != null && ` · ${tradedAgo(bond.days_since_traded)}`}
            </div>
          ) : null}
        </td>
        {/* Invested = units × price paid, with cash already received on
            this position beneath. */}
        <td className={`${TD} text-right tabular-nums`} title={investedTitle}>
          <div className="text-zinc-200">{fmt.inr(p.invested)}</div>
          <div className="text-[10px] text-zinc-500">
            {fmt.num(p.units, 2)} × {p.avg_price != null ? fmt.inrFull(p.avg_price) : "–"}
            <MetadataSeparator />
            {fmt.inr(received)} back
          </div>
        </td>
        {/* Value at LTP; beneath it the LTP and, with Kite, the live best
            bid - what you could sell into right now. */}
        <td
          className={`${TD} text-right tabular-nums`}
          title={[
            weightPct != null ? `${weightPct.toFixed(1)}% of portfolio` : null,
            bond.days_since_traded != null ? `LTP ${tradedAgo(bond.days_since_traded)}` : null,
            quote?.bid_price
              ? `Best bid ${fmt.inrFull(quote.bid_price)} × ${fmt.num(quote.bid_qty, 0)} on ${quote.bid_exchange ?? quote.exchange}${quote.bid_ytm != null ? `, yield ${fmt.pct(quote.bid_ytm)}` : ""}`
              : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        >
          <div className="font-medium text-zinc-200">{fmt.inr(p.value)}</div>
          <div className="text-[10px] text-zinc-500">
            {p.ltp != null ? `LTP ${fmt.inrFull(p.ltp)}` : "no LTP"}
            {quote !== undefined && (
              <>
                <MetadataSeparator />
                {quote?.bid_price
                  ? `bid ${fmt.inrFull(quote.bid_price)} × ${fmt.num(quote.bid_qty, 0)}`
                  : "no bid"}
              </>
            )}
          </div>
        </td>
        {/* P&L with the holding age beneath: a gain only means something
            against how long it took. */}
        <td
          className={`${TD} text-right tabular-nums`}
          title={oldest ? `Since your first purchase on ${fmt.date(oldest)}` : undefined}
        >
          {p.gain == null ? (
            <span className="text-zinc-600">–</span>
          ) : (
            <span className={p.gain >= 0 ? "text-emerald-400" : "text-red-400"}>
              {p.gain >= 0 ? "+" : "−"}
              {fmt.inr(Math.abs(p.gain))}
              {p.gain_pct != null && (
                <span className="text-[10px] opacity-80"> ({p.gain_pct.toFixed(1)}%)</span>
              )}
            </span>
          )}
          {heldLabel && <div className="text-[10px] text-zinc-500">{heldLabel}</div>}
        </td>
        {/* The bond as Explore shows it: the same cells in the same order. */}
        <RatingCell bond={bond} onSelect={openTab} />
        <PayoutTd bond={bond} today={payoutDay} onSelect={openTab} />
        <td className={TD}>
          {p.next_payment ? (
            <div title={p.next_payment.estimated ? "Estimated amount" : undefined}>
              <div className="text-zinc-200 font-medium">
                {p.next_payment.estimated && "≈"}
                {fmt.inr(p.next_payment.amount)}
              </div>
              <div className="text-[10px] text-zinc-500">
                {monthYear(p.next_payment.payment_date ?? p.next_payment.due_date)}
              </div>
            </div>
          ) : (
            <span className="text-zinc-600">–</span>
          )}
        </td>
        <MaturityCell bond={bond} />
      </tr>
      {open && (
        <tr className="border-b border-zinc-800/60 bg-zinc-900/40">
          <td colSpan={HOLDINGS_COLS.length} className="bg-zinc-950/40 px-3 py-3">
            <PositionPanel
              p={p}
              quote={quote}
              kiteConnected={kiteConnected}
              platforms={platforms}
              onSelect={onSelect}
            />
          </td>
        </tr>
      )}
    </>
  );
}

// --- Cashflow chart + grid --------------------------------------------------

const CF_RANGES = [
  ["1y", 1],
  ["3y", 3],
  ["5y", 5],
  ["All", 0],
] as const;

function CashflowChart({
  cashflows,
  net = false,
  onSelect,
}: {
  cashflows: PortfolioMonth[];
  net?: boolean;
  onSelect: (isin: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const tooltip = useRef<HTMLDivElement>(null);
  const [range, setRange] = useState<(typeof CF_RANGES)[number][0]>("5y");
  // Click-to-pin: clicking a bar freezes that month's breakdown into an
  // interactive popover (bond names open the drawer). Mirrored into a ref
  // so the chart's crosshair handler (created once per [shown]) can see it.
  const [pinned, setPinned] = useState<{ m: PortfolioMonth; rect: DOMRect } | null>(null);
  const pinnedRef = useRef(false);
  pinnedRef.current = pinned != null;

  const shown = useMemo(() => {
    const years = CF_RANGES.find(([r]) => r === range)![1];
    if (!years) return cashflows;
    const limit = new Date();
    limit.setFullYear(limit.getFullYear() + years);
    const cap = `${limit.getFullYear()}-${String(limit.getMonth() + 1).padStart(2, "0")}`;
    return cashflows.filter((c) => c.month <= cap);
  }, [cashflows, range]);

  useEffect(() => {
    // Redraw invalidates any pin anchored to the old bars' pixels.
    setPinned(null);
    if (!container.current || shown.length === 0) return;
    const chart = createChart(container.current, {
      height: 220,
      layout: {
        background: { color: "transparent" },
        textColor: "#71717a",
        fontFamily: "Inter, system-ui, sans-serif",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "rgba(113,113,122,0.12)" },
        horzLines: { color: "rgba(113,113,122,0.12)" },
      },
      rightPriceScale: { borderColor: "rgba(113,113,122,0.3)" },
      timeScale: { borderColor: "rgba(113,113,122,0.3)" },
      crosshair: { mode: 0 },
      handleScroll: false,
      handleScale: false,
      // Off: hovering the "total" series would draw it over "principal",
      // flipping the whole chart blue (default is true in v5).
      hoveredSeriesOnTop: false,
    });

    const ts = (month: string) => (new Date(`${month}-01`).getTime() / 1000) as UTCTimestamp;
    // Stacked read: total drawn behind, principal in front.
    // Both colors fully opaque: the series overlap to fake stacking, and
    // translucent bars alpha-blend into a misleading teal wherever
    // total == principal (interest-free months), shifting on repaints.
    // Past (received) months keep the hue but drop to solid muted tones —
    // still opaque, same alpha-blend reasoning.
    const totalSeries = chart.addSeries(HistogramSeries, {
      color: "#38bdf8",
      priceFormat: { type: "volume" },
      priceLineVisible: false,
    });
    totalSeries.setData(
      shown.map((c) => ({
        time: ts(c.month),
        value: cashAmount(c, net),
        color: isPastMonth(c.month) ? "#1e5c78" : "#38bdf8",
      })),
    );
    const principalSeries: ISeriesApi<"Histogram"> = chart.addSeries(HistogramSeries, {
      color: "#10b981",
      priceFormat: { type: "volume" },
      priceLineVisible: false,
    });
    principalSeries.setData(
      shown.map((c) => ({
        time: ts(c.month),
        value: c.principal,
        color: isPastMonth(c.month) ? "#0e5c44" : "#10b981",
      })),
    );

    const byTime = new Map(shown.map((c) => [ts(c.month) as number, c]));
    chart.subscribeClick((param) => {
      const cf = param.time != null ? byTime.get(param.time as number) : undefined;
      const box = container.current?.getBoundingClientRect();
      if (!cf || !param.point || !box) {
        setPinned(null);
        return;
      }
      // Zero-size anchor at the click point; MonthPopover's flip logic
      // positions around it exactly like a grid cell rect.
      setPinned({
        m: cf,
        rect: new DOMRect(box.left + param.point.x, box.top + param.point.y, 0, 0),
      });
      if (tooltip.current) tooltip.current.style.display = "none";
    });
    chart.subscribeCrosshairMove((param) => {
      const el = tooltip.current;
      if (!el) return;
      const cf = param.time != null ? byTime.get(param.time as number) : undefined;
      if (!cf || !param.point || pinnedRef.current) {
        el.style.display = "none";
        return;
      }
      const d = new Date(`${cf.month}-01`);
      const money = (v: number) => `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
      const num = `text-align:right;font-variant-numeric:tabular-nums;padding-left:12px`;
      // Already-received months read as history: dim the amounts.
      const past = isPastMonth(cf.month);
      const intCol = past ? "#38bdf880" : "#38bdf8";
      const prinCol = past ? "#10b98180" : "#10b981";
      const labelCol = past ? "#a1a1aa" : "#e4e4e7";
      const bondLines = (cf.bonds ?? [])
        .map((b) => {
          // Money already banked reads as history mid-month too.
          const banked = past || allDatesPast(b.dates);
          const li = banked ? "#38bdf880" : intCol;
          const lp = banked ? "#10b98180" : prinCol;
          const ll = banked ? "#71717a" : labelCol;
          return (
            `<tr><td style="color:${ll};padding-right:10px">${b.estimated ? "≈" : ""}${b.label}</td>` +
            `<td style="font-family:ui-monospace,monospace;color:#71717a;padding-right:2px">${b.isin}</td>` +
            `<td style="color:#71717a;${num}">${dayMonth(b.dates ?? [])}</td>` +
            `<td style="color:${li};${num}">${b.interest > 0 ? money(b.interest) : "–"}</td>` +
            `<td style="color:#fb718580;${num}">${b.interest > 0 ? `−${money(tds(b.interest))}` : "–"}</td>` +
            `<td style="color:${li};${num}">${b.interest > 0 ? money(netInterest(b.interest)) : "–"}</td>` +
            `<td style="color:${lp};${num}">${b.principal > 0 ? money(b.principal) : "–"}</td>` +
            `<td style="color:#a1a1aa;${num}">${money(cashAmount(b, net))}</td></tr>`
          );
        })
        .join("");
      el.innerHTML =
        `<div style="color:#71717a;margin-bottom:2px">${d.toLocaleDateString("en-IN", { month: "short", year: "numeric" })}${isPastMonth(cf.month) ? " | past scheduled" : ""}${cf.estimated ? " (≈ est.)" : ""}</div>` +
        `<table><thead><tr style="color:#71717a">` +
        `<td style="padding-right:10px">Bond</td>` +
        `<td style="padding-right:2px">ISIN</td>` +
        `<td style="${num}">Date</td>` +
        `<td style="color:${intCol};${num}">Interest</td>` +
        `<td style="color:#fb718580;${num}">TDS</td>` +
        `<td style="color:${intCol};${num}">Net</td>` +
        `<td style="color:${prinCol};${num}">Principal</td>` +
        `<td style="${num}">Total${net ? " (net)" : ""}</td>` +
        `</tr></thead><tbody>${bondLines}` +
        `<tr style="border-top:1px solid #3f3f46;font-weight:500">` +
        `<td style="color:#71717a;padding-right:2px">Total</td><td></td><td></td>` +
        `<td style="color:${intCol};${num}">${cf.interest > 0 ? money(cf.interest) : "–"}</td>` +
        `<td style="color:#fb718580;${num}">${cf.interest > 0 ? `−${money(tds(cf.interest))}` : "–"}</td>` +
        `<td style="color:${intCol};${num}">${cf.interest > 0 ? money(netInterest(cf.interest)) : "–"}</td>` +
        `<td style="color:${prinCol};${num}">${cf.principal > 0 ? money(cf.principal) : "–"}</td>` +
        `<td style="color:#e4e4e7;${num}">${money(cashAmount(cf, net))}</td>` +
        `</tr></tbody></table>`;
      el.style.display = "block";
      // Flip using the rendered width so long bond names never overflow.
      const w = container.current?.clientWidth ?? 0;
      const flip = param.point.x + 14 + el.offsetWidth > w;
      el.style.left = flip ? "auto" : `${param.point.x + 14}px`;
      el.style.right = flip ? `${w - param.point.x + 14}px` : "auto";
      el.style.top = `${param.point.y + 10}px`;
    });

    chart.timeScale().fitContent();
    const onResize = () =>
      container.current && chart.applyOptions({ width: container.current.clientWidth });
    window.addEventListener("resize", onResize);
    onResize();
    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
    };
  }, [shown, net]);

  return (
    <div className="border-b border-zinc-800 bg-zinc-900/30 p-3">
      <div className="flex items-center gap-3 mb-2">
        <div className="flex gap-1">
          {CF_RANGES.map(([r]) => (
            <button
              key={r}
              type="button"
              onClick={() => setRange(r)}
              className={`px-2 py-0.5 rounded text-[11px] font-medium ${
                range === r ? "bg-zinc-700 text-zinc-100" : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {r}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-3 text-[10px] text-zinc-500">
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-sm bg-sky-400" /> interest
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-sm bg-emerald-500" /> principal
          </span>
        </div>
      </div>
      <div className="relative">
        <div ref={container} />
        <div
          ref={tooltip}
          className="absolute z-10 hidden pointer-events-none rounded-md border border-zinc-700 bg-zinc-900/95 px-2.5 py-1.5 text-[11px] leading-relaxed"
          style={{ display: "none" }}
        />
      </div>
      {pinned && (
        <MonthPopover
          m={pinned.m}
          year={Number(pinned.m.month.slice(0, 4))}
          monthIdx={Number(pinned.m.month.slice(5, 7)) - 1}
          rect={pinned.rect}
          net={net}
          pinned
          onSelectBond={(isin) => {
            setPinned(null);
            onSelect(isin);
          }}
          onClose={() => setPinned(null)}
        />
      )}
    </div>
  );
}

/** Hover detail for one grid cell: which bonds pay that month, split
 * interest/principal. Rendered position:fixed so the grid's overflow-x
 * scroll container cannot clip it (absolute popovers were cut off).
 *
 * ``pinned`` (click-to-pin) makes it interactive: bond names become
 * links into the drawer, a backdrop click or Esc dismisses. */
type HoverCell = { m: PortfolioMonth; year: number; monthIdx: number; rect: DOMRect };

function MonthPopover({
  m,
  year,
  monthIdx,
  rect,
  net = false,
  pinned,
  onSelectBond,
  onClose,
}: HoverCell & {
  net?: boolean;
  pinned?: boolean;
  onSelectBond?: (isin: string) => void;
  onClose?: () => void;
}) {
  useEffect(() => {
    if (!pinned) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose?.();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pinned, onClose]);

  const alignRight = rect.left > window.innerWidth / 2;
  const below = rect.top < 240;
  // Already-received months read as history: dim the amounts.
  const past = isPastMonth(m.month);
  const intCls = past ? "text-sky-400/50" : "text-sky-400";
  const prinCls = past ? "text-emerald-400/50" : "text-emerald-400";
  const labelCls = past ? "text-zinc-500" : "text-zinc-300";
  return (
    <>
      {pinned && <div className="fixed inset-0 z-20" onClick={onClose} aria-hidden="true" />}
      <div
        className={`${pinned ? "" : "pointer-events-none "}fixed z-30 w-max rounded-md border border-zinc-700 bg-zinc-900/95 px-2.5 py-1.5 text-left text-[11px] leading-relaxed shadow-xl`}
        style={{
          ...(alignRight ? { right: window.innerWidth - rect.right } : { left: rect.left }),
          ...(below ? { top: rect.bottom + 2 } : { bottom: window.innerHeight - rect.top + 2 }),
        }}
      >
        <div className="text-zinc-500 mb-1">
          {MONTHS[monthIdx]} {year}
          <MetadataSeparator />
          {fmt.inr(cashAmount(m, net))}
          {net && " net"}
          {isPastMonth(m.month) && (
            <span className="text-zinc-600">
              <MetadataSeparator />
              past scheduled
            </span>
          )}
          {m.estimated && " (≈ est.)"}
        </div>
        <table className="tabular-nums">
          <thead>
            <tr className="text-zinc-600">
              <td className="pr-3">Bond</td>
              <td className="pr-2.5">ISIN</td>
              <td
                className="pr-3 text-right"
                title="When the money lands (payment date; scheduled due date when none published)"
              >
                Date
              </td>
              <td className="pr-3 text-right">Interest</td>
              <td
                className="pr-3 text-right"
                title={`${TDS_RATE * 100}% TDS on interest (Section 193, listed debentures, PAN on record). Principal is not taxed at source.`}
              >
                TDS
              </td>
              <td className="pr-3 text-right">Net</td>
              <td className="text-right">Principal</td>
            </tr>
          </thead>
          <tbody>
            {m.bonds.map((b) => {
              // Money already banked reads as history even when the
              // month itself is still running.
              const banked = past || allDatesPast(b.dates);
              return (
                <tr
                  key={b.isin}
                  title={
                    banked && !past ? "Scheduled date has passed; receipt not verified" : undefined
                  }
                >
                  <td className={`pr-3 ${banked ? "text-zinc-500" : labelCls}`}>
                    {b.estimated && <span className="text-zinc-500">≈</span>}
                    {pinned && onSelectBond ? (
                      <button
                        type="button"
                        title="View bond details"
                        onClick={() => onSelectBond(b.isin)}
                        className="text-left hover:text-emerald-400 hover:underline underline-offset-2"
                      >
                        {b.label}
                      </button>
                    ) : (
                      b.label
                    )}
                  </td>
                  <td className="pr-2.5 font-mono text-zinc-500">
                    <span className="inline-flex items-center gap-1">
                      {b.isin}
                      {pinned && <CopyBtn text={b.isin} label="ISIN" />}
                    </span>
                  </td>
                  <td className="pr-3 text-right text-zinc-500">{dayMonth(b.dates ?? [])}</td>
                  <td className={`pr-3 text-right ${banked ? "text-sky-400/50" : intCls}`}>
                    {b.interest > 0 ? fmt.inr(b.interest) : "–"}
                  </td>
                  <td className="pr-3 text-right text-rose-400/60">
                    {b.interest > 0 ? `−${fmt.inr(tds(b.interest))}` : "–"}
                  </td>
                  <td className={`pr-3 text-right ${banked ? "text-sky-400/50" : intCls}`}>
                    {b.interest > 0 ? fmt.inr(netInterest(b.interest)) : "–"}
                  </td>
                  <td className={`text-right ${banked ? "text-emerald-400/50" : prinCls}`}>
                    {b.principal > 0 ? fmt.inr(b.principal) : "–"}
                  </td>
                </tr>
              );
            })}
            <tr className="border-t border-zinc-800 font-medium">
              <td className="pr-3 text-zinc-500">Total</td>
              <td />
              <td />
              <td className={`pr-3 text-right ${intCls}`}>
                {m.interest > 0 ? fmt.inr(m.interest) : "–"}
              </td>
              <td className="pr-3 text-right text-rose-400/60">
                {m.interest > 0 ? `−${fmt.inr(tds(m.interest))}` : "–"}
              </td>
              <td className={`pr-3 text-right ${intCls}`}>
                {m.interest > 0 ? fmt.inr(netInterest(m.interest)) : "–"}
              </td>
              <td className={`text-right ${prinCls}`}>
                {m.principal > 0 ? fmt.inr(m.principal) : "–"}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </>
  );
}

function CashflowGrid({
  cashflows,
  net = false,
  onSelect,
}: {
  cashflows: PortfolioMonth[];
  net?: boolean;
  onSelect: (isin: string) => void;
}) {
  const rows = monthGrid(cashflows, net);
  const [hover, setHover] = useState<HoverCell | null>(null);
  const [pinned, setPinned] = useState<HoverCell | null>(null);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-zinc-500 border-b border-zinc-800">
            <th className={TH}>Year</th>
            {MONTHS.map((m) => (
              <th key={m} className={`${TH} text-right`}>
                {m}
              </th>
            ))}
            <th className={`${TH} text-right`}>Total</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.year} className="border-b border-zinc-800/60">
              <td className={`${TD} font-medium text-zinc-200`}>{r.year}</td>
              {r.months.map((m, i) => (
                <td
                  key={MONTHS[i]}
                  className={`${TD} text-right tabular-nums ${m ? "cursor-pointer " : ""}${
                    m && isPastMonth(m.month)
                      ? "text-zinc-600"
                      : m && m.principal > 0
                        ? "text-emerald-400"
                        : m
                          ? "text-sky-400"
                          : "text-zinc-400"
                  }`}
                  onMouseEnter={(e) =>
                    m &&
                    setHover({
                      m,
                      year: r.year,
                      monthIdx: i,
                      rect: e.currentTarget.getBoundingClientRect(),
                    })
                  }
                  onMouseLeave={() => setHover(null)}
                  onClick={(e) => {
                    if (!m) return;
                    setHover(null);
                    setPinned({
                      m,
                      year: r.year,
                      monthIdx: i,
                      rect: e.currentTarget.getBoundingClientRect(),
                    });
                  }}
                >
                  {m ? (
                    <>
                      {m.estimated && <span className="text-zinc-500">≈</span>}
                      {fmt.inr(cashAmount(m, net))}
                    </>
                  ) : (
                    <span className="text-zinc-700">–</span>
                  )}
                </td>
              ))}
              <td className={`${TD} text-right tabular-nums font-medium text-zinc-200`}>
                {fmt.inr(r.total)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {hover && !pinned && <MonthPopover {...hover} net={net} />}
      {pinned && (
        <MonthPopover
          {...pinned}
          net={net}
          pinned
          onSelectBond={(isin) => {
            setPinned(null);
            onSelect(isin);
          }}
          onClose={() => setPinned(null)}
        />
      )}
    </div>
  );
}

// --- News --------------------------------------------------------------------

function NewsList({ news, onSelect }: { news: PortfolioNews[]; onSelect: (isin: string) => void }) {
  return (
    <div className="divide-y divide-zinc-800/60">
      {news.map((n) => (
        <div
          key={`${n.isin}-${n.agency}-${n.rating_date}-${n.rating}`}
          className="flex items-center gap-3 px-3 py-2 text-xs"
        >
          <button
            type="button"
            onClick={() => onSelect(n.isin)}
            className="font-medium text-zinc-200 hover:text-emerald-400 text-left max-w-56 truncate"
            title={n.isin}
          >
            {n.issuer_name ?? n.isin}
          </button>
          <span className="text-zinc-400 flex-1 min-w-0 truncate">
            {n.agency && <span className="text-zinc-500">{n.agency}: </span>}
            {n.action ? `${n.action} — ` : ""}
            {n.rating ?? ""}
          </span>
          <span className="inline-flex items-center whitespace-nowrap">
            <InlineMetadata
              parts={[
                n.rating_date && (
                  <span key="date" className="text-zinc-600">
                    {new Date(n.rating_date).toLocaleDateString("en-IN")}
                  </span>
                ),
                n.press_release && (
                  <a
                    key="pr"
                    href={n.press_release}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="text-emerald-400 hover:text-emerald-300 whitespace-nowrap"
                  >
                    PR ↗
                  </a>
                ),
              ]}
            />
          </span>
        </div>
      ))}
    </div>
  );
}

export { MonthPopover, NewsList, PositionRow };
