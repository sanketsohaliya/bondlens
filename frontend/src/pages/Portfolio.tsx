import {
  createChart,
  HistogramSeries,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { fmt } from "../api/client";
import type { PortfolioMonth, PortfolioNews, PortfolioPosition } from "../api/types";
import Donut from "../components/Donut";
import LotsPanel from "../components/LotsPanel";
import RatingBadge from "../components/RatingBadge";
import StatBox from "../components/StatBox";
import { monthGrid, usePortfolio, type WeightBy, weights } from "../lib/portfolio";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const monthYear = (d: string | null) =>
  d ? new Date(d).toLocaleDateString("en-IN", { month: "short", year: "numeric" }) : "–";

interface SliceFilter {
  by: WeightBy;
  label: string;
  isins: string[];
}

export default function Portfolio({ onSelect }: { onSelect: (isin: string) => void }) {
  const { data, isLoading, error } = usePortfolio();
  const [filter, setFilter] = useState<SliceFilter | null>(null);

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
          Click the <span className="text-zinc-300">◇</span> next to any bond in the screener to
          record a purchase — units, price paid, date and platform.
        </p>
      </div>
    );
  }

  const { summary, positions, cashflows, news } = data;
  const shown = filter ? positions.filter((p) => filter.isins.includes(p.isin)) : positions;

  const pickSlice = (by: WeightBy) => (label: string | null) => {
    if (label === null) {
      setFilter(null);
      return;
    }
    const slice = bySlice[by].find((s) => s.label === label);
    setFilter(slice ? { by, label, isins: slice.isins } : null);
  };

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
        <StatBox label="Total invested" value={fmt.inr(summary.invested)} />
        <StatBox label="Current value" value={fmt.inr(summary.value)} accent />
        <GainBox gain={summary.gain} invested={summary.invested} />
        <StatBox label="Weighted avg YTM" value={fmt.pct(summary.weighted_ytm)} />
        <StatBox label="Weighted avg maturity" value={fmt.yearsMonths(summary.weighted_years)} />
        <StatBox label="Issuers" value={`${summary.issuers} · ${summary.bonds} bonds`} />
      </div>

      <section>
        <SectionTitle>Holdings</SectionTitle>
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
      </section>

      {cashflows.length > 0 && (
        <section>
          <SectionTitle>Cashflow to maturity</SectionTitle>
          <CashflowChart cashflows={cashflows} />
          <CashflowGrid cashflows={cashflows} />
        </section>
      )}

      <section>
        <SectionTitle>Investment breakdown</SectionTitle>
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
      </section>

      {news.length > 0 && (
        <section>
          <SectionTitle>Market news · rating actions on your issuers</SectionTitle>
          <NewsList news={news} onSelect={onSelect} />
        </section>
      )}
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="flex items-center gap-2 text-xs font-bold text-zinc-200 uppercase tracking-wider mb-2.5">
      <span className="h-3.5 w-1 rounded-full bg-emerald-500/80" />
      {children}
      <span className="flex-1 border-t border-zinc-800" />
    </h2>
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

function HoldingsTable({
  positions,
  onSelect,
}: {
  positions: PortfolioPosition[];
  onSelect: (isin: string) => void;
}) {
  const [openIsin, setOpenIsin] = useState<string | null>(null);
  return (
    <div className="rounded-lg border border-zinc-800 overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-zinc-500 border-b border-zinc-800">
            <th className={TH}>Issuer</th>
            <th className={TH}>Rating</th>
            <th className={`${TH} text-right`}>Units</th>
            <th className={`${TH} text-right`}>Invested</th>
            <th className={`${TH} text-right`}>LTP</th>
            <th className={`${TH} text-right`}>Value</th>
            <th className={`${TH} text-right`}>P&L</th>
            <th className={`${TH} text-right`}>YTM</th>
            <th className={TH}>Maturity</th>
            <th className={TH}>Next payment</th>
            <th className={TH} />
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <PositionRow
              key={p.isin}
              p={p}
              open={openIsin === p.isin}
              onToggle={() => setOpenIsin(openIsin === p.isin ? null : p.isin)}
              onSelect={() => onSelect(p.isin)}
            />
          ))}
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
        onClick={onSelect}
        className="border-b border-zinc-800/60 hover:bg-zinc-900/60 cursor-pointer"
      >
        <td className={TD}>
          <div className="font-medium text-zinc-200 max-w-56 truncate">
            {p.issuer_name ?? p.isin}
          </div>
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
        <td
          className={`${TD} text-right`}
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
        >
          <button
            type="button"
            className="text-zinc-500 hover:text-zinc-200 px-1"
            title={open ? "Hide lots" : `${p.lots.length} lot${p.lots.length > 1 ? "s" : ""}`}
          >
            {p.lots.length} {open ? "▴" : "▾"}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="border-b border-zinc-800/60 bg-zinc-900/40">
          <td colSpan={11} className="px-3 py-2">
            <LotsPanel p={p} />
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
    });

    const ts = (month: string) => (new Date(`${month}-01`).getTime() / 1000) as UTCTimestamp;
    // Stacked read: total drawn behind, principal in front.
    const totalSeries = chart.addSeries(HistogramSeries, {
      color: "rgba(56,189,248,0.65)",
      priceFormat: { type: "volume" },
      priceLineVisible: false,
    });
    totalSeries.setData(shown.map((c) => ({ time: ts(c.month), value: c.interest + c.principal })));
    const principalSeries: ISeriesApi<"Histogram"> = chart.addSeries(HistogramSeries, {
      color: "rgba(16,185,129,0.9)",
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
      el.innerHTML =
        `<div style="color:#71717a;margin-bottom:2px">${d.toLocaleDateString("en-IN", { month: "short", year: "numeric" })}${cf.estimated ? " (≈ est.)" : ""}</div>` +
        `<span style="color:#38bdf8">Interest</span> ${money(cf.interest)}<br/>` +
        `<span style="color:#10b981">Principal</span> ${money(cf.principal)}<br/>` +
        `<span style="color:#e4e4e7">Total</span> ${money(cf.interest + cf.principal)}`;
      el.style.display = "block";
      const w = container.current?.clientWidth ?? 0;
      const flip = param.point.x > w - 160;
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
            <span className="w-2 h-2 rounded-sm bg-sky-400/70" /> interest
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

function CashflowGrid({ cashflows }: { cashflows: PortfolioMonth[] }) {
  const rows = monthGrid(cashflows);
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
                  title={
                    m
                      ? `Interest ${fmt.inr(m.interest)} · Principal ${fmt.inr(m.principal)}`
                      : undefined
                  }
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
