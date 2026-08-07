import {
  createChart,
  HistogramSeries,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { fmt } from "../api/client";
import type { PortfolioMonth, PortfolioNews, PortfolioPosition } from "../api/types";
import AddLotSearch from "../components/AddLotSearch";
import Donut from "../components/Donut";
import { BookmarkIcon } from "../components/ExploreTable";
import LotsPanel from "../components/LotsPanel";
import RatingBadge from "../components/RatingBadge";
import StatBox from "../components/StatBox";
import { fuzzyMatch } from "../lib/fuzzy";
import { monthGrid, usePortfolio, type WeightBy, weights } from "../lib/portfolio";

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

export default function Portfolio({ onSelect }: { onSelect: (isin: string) => void }) {
  const { data, isLoading, error } = usePortfolio();
  const [filter, setFilter] = useState<SliceFilter | null>(null);
  const [nameQuery, setNameQuery] = useState("");
  const [tab, setTab] = useState<PortfolioTab>("holdings");

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

  const { summary, positions, cashflows, news } = data;
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
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
        <StatBox label="Total invested" value={fmt.inr(summary.invested)} />
        <StatBox label="Current value" value={fmt.inr(summary.value)} accent />
        <GainBox gain={summary.gain} invested={summary.invested} />
        <StatBox label="Weighted avg YTM" value={fmt.pct(summary.weighted_ytm)} />
        <StatBox label="Weighted avg maturity" value={fmt.yearsMonths(summary.weighted_years)} />
        <StatBox
          label="Diversification"
          value={`${summary.issuers} issuer${summary.issuers === 1 ? "" : "s"} · ${summary.bonds} bond${summary.bonds === 1 ? "" : "s"}`}
        />
      </div>

      <section>
        <div className="flex items-center gap-1 border-b border-zinc-800 mb-3">
          {PORTFOLIO_TABS.map(([t, label]) => (
            <button
              type="button"
              key={t}
              onClick={() => setTab(t)}
              className={
                "text-xs px-3 py-1.5 rounded-t-md border-b-2 transition-colors -mb-px " +
                (tab === t
                  ? "border-emerald-500 text-emerald-400"
                  : "border-transparent text-zinc-500 hover:text-zinc-300")
              }
            >
              {label}
            </button>
          ))}
          <span className="flex-1" />
          {tab === "holdings" && (
            <span className="flex items-center gap-2 pb-1.5">
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

        {tab === "holdings" && (
          <>
            {filter && (
              <div className="mb-2">
                <button
                  type="button"
                  onClick={() => setFilter(null)}
                  className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md border border-zinc-600 bg-zinc-800/60 text-xs text-zinc-200 hover:border-zinc-400"
                >
                  {filter.by}: {filter.label}
                  <span className="text-zinc-500">×</span>
                </button>
              </div>
            )}
            <HoldingsTable positions={shown} onSelect={onSelect} />
          </>
        )}

        {tab === "cashflow" &&
          (cashflows.length > 0 ? (
            <>
              <CashflowChart cashflows={cashflows} />
              <CashflowGrid cashflows={cashflows} />
            </>
          ) : (
            <div className="py-12 text-center text-sm text-zinc-600">No future cashflows</div>
          ))}

        {tab === "breakdown" && (
          <>
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
          </>
        )}

        {tab === "news" &&
          (news.length > 0 ? (
            <NewsList news={news} onSelect={onSelect} />
          ) : (
            <div className="py-12 text-center text-sm text-zinc-600">
              No rating actions on your issuers
            </div>
          ))}
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
  | "rating"
  | "units"
  | "invested"
  | "ltp"
  | "value"
  | "gain"
  | "ytm"
  | "maturity"
  | "next_payment";

/** Sort accessors — semantic, not display, values: rating by rank (lower =
 * better), maturity by remaining tenor, next payment by due date. */
const HOLDINGS_ACCESSORS: Record<
  HoldingsSortKey,
  (p: PortfolioPosition) => number | string | null | undefined
> = {
  issuer: (p) => (p.issuer_name ?? p.isin).toLowerCase(),
  rating: (p) => p.rating_rank,
  units: (p) => p.units,
  invested: (p) => p.invested,
  ltp: (p) => p.ltp,
  value: (p) => p.value,
  gain: (p) => p.gain,
  ytm: (p) => p.ytm,
  maturity: (p) => p.years_to_maturity,
  next_payment: (p) => p.next_payment?.due_date,
};

const HOLDINGS_COLS: Array<{ label: string; key: HoldingsSortKey | null; right?: boolean }> = [
  { label: "", key: null }, // expand chevron
  { label: "Issuer", key: "issuer" },
  { label: "Rating", key: "rating" },
  { label: "Units", key: "units", right: true },
  { label: "Invested", key: "invested", right: true },
  { label: "LTP", key: "ltp", right: true },
  { label: "Value", key: "value", right: true },
  { label: "P&L", key: "gain", right: true },
  { label: "YTM", key: "ytm", right: true },
  { label: "Maturity", key: "maturity" },
  { label: "Next payment", key: "next_payment" },
];

function HoldingsTable({
  positions,
  onSelect,
}: {
  positions: PortfolioPosition[];
  onSelect: (isin: string) => void;
}) {
  const [openIsin, setOpenIsin] = useState<string | null>(null);
  const [sort, setSort] = useState<{ key: HoldingsSortKey; dir: 1 | -1 }>({
    key: "value",
    dir: -1, // matches the backend's default biggest-position-first order
  });

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
    <div className="rounded-lg border border-zinc-800 overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-zinc-500 border-b border-zinc-800">
            {HOLDINGS_COLS.map((c, i) => (
              <th
                key={c.label || i}
                className={`${TH} ${c.right ? "text-right" : ""} ${
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
                <span className={c.key === sort.key ? "text-zinc-200" : undefined}>{c.label}</span>
                {c.key === sort.key && (
                  <span className="ml-1 text-emerald-500/80">{sort.dir === -1 ? "▼" : "▲"}</span>
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
              open={openIsin === p.isin}
              onToggle={() => setOpenIsin(openIsin === p.isin ? null : p.isin)}
              onSelect={() => onSelect(p.isin)}
            />
          ))}
          {sorted.length === 0 && (
            <tr>
              <td colSpan={11} className="px-3 py-6 text-center text-zinc-600">
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
  open,
  onToggle,
  onSelect,
}: {
  p: PortfolioPosition;
  open: boolean;
  onToggle: () => void;
  onSelect: () => void;
}) {
  return (
    <>
      <tr
        onClick={onToggle}
        aria-expanded={open}
        title={open ? "Click row to hide lots" : "Click row to show lots"}
        className="border-b border-zinc-800/60 hover:bg-zinc-900/60 cursor-pointer"
      >
        <td className="w-6 pl-3 pr-0">
          <span
            className={`inline-block text-zinc-500 transition-transform duration-150 ${
              open ? "rotate-90 text-emerald-400" : ""
            }`}
            aria-hidden="true"
          >
            ›
          </span>
        </td>
        <td className={TD}>
          <button
            type="button"
            title="View bond details"
            onClick={(e) => {
              e.stopPropagation();
              onSelect();
            }}
            className="block font-medium text-zinc-200 max-w-56 truncate text-left hover:text-emerald-400 hover:underline underline-offset-2"
          >
            {p.issuer_name ?? p.isin}
          </button>
          <div className="text-[10px] text-zinc-600">{p.isin}</div>
        </td>
        <td className={TD}>
          <span title={p.credit_rating ?? undefined}>
            <RatingBadge rating={p.rating_grade} showAgency={false} />
          </span>
        </td>
        <td className={`${TD} text-right tabular-nums`}>{fmt.num(p.units, 2)}</td>
        <td className={`${TD} text-right tabular-nums`}>{fmt.inr(p.invested)}</td>
        <td className={`${TD} text-right tabular-nums`}>{fmt.inrFull(p.ltp)}</td>
        <td className={`${TD} text-right tabular-nums font-medium text-zinc-200`}>
          {fmt.inr(p.value)}
        </td>
        <td className={`${TD} text-right tabular-nums`}>
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
        </td>
        <td className={`${TD} text-right tabular-nums`}>{fmt.pct(p.ytm)}</td>
        <td className={TD}>
          {monthYear(p.maturity_date)}
          {p.years_to_maturity != null && (
            <span className="text-zinc-600"> · {fmt.yearsMonths(p.years_to_maturity)}</span>
          )}
        </td>
        <td className={TD}>
          {p.next_payment ? (
            <span title={p.next_payment.estimated ? "Estimated amount" : undefined}>
              {p.next_payment.estimated && "≈"}
              {fmt.inr(p.next_payment.amount)}
              <span className="text-zinc-600"> · {monthYear(p.next_payment.due_date)}</span>
            </span>
          ) : (
            <span className="text-zinc-600">–</span>
          )}
        </td>
      </tr>
      {open && (
        <tr className="border-b border-zinc-800/60 bg-zinc-900/40">
          <td colSpan={11} className="px-3 py-2">
            <LotsPanel p={p} onViewBond={onSelect} />
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

function CashflowChart({ cashflows }: { cashflows: PortfolioMonth[] }) {
  const container = useRef<HTMLDivElement>(null);
  const tooltip = useRef<HTMLDivElement>(null);
  const [range, setRange] = useState<(typeof CF_RANGES)[number][0]>("5y");

  const shown = useMemo(() => {
    const years = CF_RANGES.find(([r]) => r === range)![1];
    if (!years) return cashflows;
    const limit = new Date();
    limit.setFullYear(limit.getFullYear() + years);
    const cap = `${limit.getFullYear()}-${String(limit.getMonth() + 1).padStart(2, "0")}`;
    return cashflows.filter((c) => c.month <= cap);
  }, [cashflows, range]);

  useEffect(() => {
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
    const totalSeries = chart.addSeries(HistogramSeries, {
      color: "#38bdf8",
      priceFormat: { type: "volume" },
      priceLineVisible: false,
    });
    totalSeries.setData(shown.map((c) => ({ time: ts(c.month), value: c.interest + c.principal })));
    const principalSeries: ISeriesApi<"Histogram"> = chart.addSeries(HistogramSeries, {
      color: "#10b981",
      priceFormat: { type: "volume" },
      priceLineVisible: false,
    });
    principalSeries.setData(shown.map((c) => ({ time: ts(c.month), value: c.principal })));

    const byTime = new Map(shown.map((c) => [ts(c.month) as number, c]));
    chart.subscribeCrosshairMove((param) => {
      const el = tooltip.current;
      if (!el) return;
      const cf = param.time != null ? byTime.get(param.time as number) : undefined;
      if (!cf || !param.point) {
        el.style.display = "none";
        return;
      }
      const d = new Date(`${cf.month}-01`);
      const money = (v: number) => `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
      const num = `text-align:right;font-variant-numeric:tabular-nums;padding-left:12px`;
      const bondLines = (cf.bonds ?? [])
        .map(
          (b) =>
            `<tr><td style="color:#e4e4e7;padding-right:2px">${b.estimated ? "≈" : ""}${b.label}</td>` +
            `<td style="color:#71717a;${num}">${dayMonth(b.dates ?? [])}</td>` +
            `<td style="color:#38bdf8;${num}">${b.interest > 0 ? money(b.interest) : "–"}</td>` +
            `<td style="color:#10b981;${num}">${b.principal > 0 ? money(b.principal) : "–"}</td>` +
            `<td style="color:#a1a1aa;${num}">${money(b.interest + b.principal)}</td></tr>`,
        )
        .join("");
      el.innerHTML =
        `<div style="color:#71717a;margin-bottom:2px">${d.toLocaleDateString("en-IN", { month: "short", year: "numeric" })}${cf.estimated ? " (≈ est.)" : ""}</div>` +
        `<table><thead><tr style="color:#71717a">` +
        `<td style="padding-right:2px">Bond</td>` +
        `<td style="${num}">Date</td>` +
        `<td style="color:#38bdf8;${num}">Interest</td>` +
        `<td style="color:#10b981;${num}">Principal</td>` +
        `<td style="${num}">Total</td>` +
        `</tr></thead><tbody>${bondLines}` +
        `<tr style="border-top:1px solid #3f3f46;font-weight:500">` +
        `<td style="color:#71717a;padding-right:2px">Total</td><td></td>` +
        `<td style="color:#38bdf8;${num}">${cf.interest > 0 ? money(cf.interest) : "–"}</td>` +
        `<td style="color:#10b981;${num}">${cf.principal > 0 ? money(cf.principal) : "–"}</td>` +
        `<td style="color:#e4e4e7;${num}">${money(cf.interest + cf.principal)}</td>` +
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
  }, [shown]);

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3 mb-3">
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
    </div>
  );
}

/** Hover detail for one grid cell: which bonds pay that month, split
 * interest/principal. Rendered position:fixed so the grid's overflow-x
 * scroll container cannot clip it (absolute popovers were cut off). */
type HoverCell = { m: PortfolioMonth; year: number; monthIdx: number; rect: DOMRect };

function MonthPopover({ m, year, monthIdx, rect }: HoverCell) {
  const alignRight = rect.left > window.innerWidth / 2;
  const below = rect.top < 240;
  return (
    <div
      className="pointer-events-none fixed z-30 w-max rounded-md border border-zinc-700 bg-zinc-900/95 px-2.5 py-1.5 text-left text-[11px] leading-relaxed shadow-xl"
      style={{
        ...(alignRight ? { right: window.innerWidth - rect.right } : { left: rect.left }),
        ...(below ? { top: rect.bottom + 2 } : { bottom: window.innerHeight - rect.top + 2 }),
      }}
    >
      <div className="text-zinc-500 mb-1">
        {MONTHS[monthIdx]} {year} · {fmt.inr(m.interest + m.principal)}
        {m.estimated && " (≈ est.)"}
      </div>
      <table className="tabular-nums">
        <thead>
          <tr className="text-zinc-600">
            <td className="pr-3">Bond</td>
            <td className="pr-3 text-right">Date</td>
            <td className="pr-3 text-right">Interest</td>
            <td className="text-right">Principal</td>
          </tr>
        </thead>
        <tbody>
          {m.bonds.map((b) => (
            <tr key={b.isin}>
              <td className="pr-3 text-zinc-300">
                {b.estimated && <span className="text-zinc-500">≈</span>}
                {b.label}
              </td>
              <td className="pr-3 text-right text-zinc-500">{dayMonth(b.dates ?? [])}</td>
              <td className="pr-3 text-right text-sky-400">
                {b.interest > 0 ? fmt.inr(b.interest) : "–"}
              </td>
              <td className="text-right text-emerald-400">
                {b.principal > 0 ? fmt.inr(b.principal) : "–"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CashflowGrid({ cashflows }: { cashflows: PortfolioMonth[] }) {
  const rows = monthGrid(cashflows);
  const [hover, setHover] = useState<HoverCell | null>(null);
  return (
    <div className="rounded-lg border border-zinc-800 overflow-x-auto">
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
                  className={`${TD} text-right tabular-nums ${
                    m && m.principal > 0 ? "text-emerald-400" : "text-zinc-400"
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
                >
                  {m ? (
                    <>
                      {m.estimated && <span className="text-zinc-500">≈</span>}
                      {fmt.inr(m.interest + m.principal)}
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
      {hover && <MonthPopover {...hover} />}
    </div>
  );
}

// --- News --------------------------------------------------------------------

function NewsList({ news, onSelect }: { news: PortfolioNews[]; onSelect: (isin: string) => void }) {
  return (
    <div className="rounded-lg border border-zinc-800 divide-y divide-zinc-800/60">
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
          <span className="text-zinc-600 whitespace-nowrap">
            {n.rating_date ? new Date(n.rating_date).toLocaleDateString("en-IN") : ""}
          </span>
          {n.press_release && (
            <a
              href={n.press_release}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="text-emerald-400 hover:text-emerald-300 whitespace-nowrap"
            >
              PR ↗
            </a>
          )}
        </div>
      ))}
    </div>
  );
}
