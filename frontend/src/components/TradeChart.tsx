import { useQuery } from "@tanstack/react-query";
import {
  AreaSeries,
  createChart,
  HistogramSeries,
  type IChartApi,
  LineSeries,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, fmt } from "../api/client";
import StatBox from "./StatBox";

const RANGES = [
  ["1D", 1],
  ["1W", 7],
  ["1M", 31],
  ["6M", 183],
  ["1Y", 366],
  ["5Y", 1830],
  ["All", 0],
] as const;

export default function TradeChart({
  isin,
  faceValue,
}: {
  isin: string;
  faceValue: number | null;
}) {
  const [range, setRange] = useState<(typeof RANGES)[number][0]>("1Y");
  const [exchange, setExchange] = useState<"" | "NSE" | "BSE">("");
  const [showOtc, setShowOtc] = useState(true);
  const container = useRef<HTMLDivElement>(null);
  const tooltip = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["trades", isin],
    queryFn: () => api.trades(isin),
  });

  const filtered = useMemo(() => {
    if (!data) return [];
    const days = RANGES.find(([r]) => r === range)![1];
    // Ranges anchor to the most recent trade date, not the wall clock, so
    // "1D" always shows the last trading day with data.
    const anchor = data.trades.length
      ? Math.max(...data.trades.map((t) => new Date(t.trade_date).getTime()))
      : Date.now();
    const cutoff = days ? anchor - (days - 1) * 86400_000 : 0;
    return data.trades.filter(
      (t) => new Date(t.trade_date).getTime() >= cutoff && (!exchange || t.exchange === exchange),
    );
  }, [data, range, exchange]);

  useEffect(() => {
    if (!container.current || filtered.length === 0) return;

    const chart = createChart(container.current, {
      height: 340,
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
    });
    chartRef.current = chart;

    const ts = (d: string) => (new Date(d).getTime() / 1000) as UTCTimestamp;
    // biome-ignore lint/suspicious/noExplicitAny: lightweight-charts series types
    let retailSeries: any = null;
    // biome-ignore lint/suspicious/noExplicitAny: lightweight-charts series types
    let otcSeries: any = null;
    // biome-ignore lint/suspicious/noExplicitAny: lightweight-charts series types
    let volSeries: any = null;

    // Retail dirty price (₹) — merge NSE+BSE by date, prefer higher-turnover.
    const retailByDate = new Map<string, { close: number; volume: number }>();
    for (const t of filtered) {
      if (t.segment !== "retail" || t.close == null) continue;
      const prev = retailByDate.get(t.trade_date);
      if (!prev || (t.turnover ?? 0) > 0) {
        retailByDate.set(t.trade_date, {
          close: Number(t.close),
          volume: Number(t.volume ?? 0),
        });
      }
    }
    const retail = [...retailByDate.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([d, v]) => ({ time: ts(d), ...v }));

    if (retail.length) {
      const area = chart.addSeries(AreaSeries, {
        lineColor: "#10b981",
        topColor: "rgba(16,185,129,0.25)",
        bottomColor: "rgba(16,185,129,0.02)",
        lineWidth: 2,
        priceLineVisible: false,
      });
      area.setData(retail.map((r) => ({ time: r.time, value: r.close })));
      retailSeries = area;

      const vol = chart.addSeries(HistogramSeries, {
        priceScaleId: "vol",
        color: "rgba(113,113,122,0.5)",
        priceFormat: { type: "volume" },
      });
      chart.priceScale("vol").applyOptions({
        scaleMargins: { top: 0.82, bottom: 0 },
      });
      vol.setData(retail.map((r) => ({ time: r.time, value: r.volume })));
      volSeries = vol;
    }

    // OTC: clean % converted to ₹ via face value (approximate comparability).
    if (showOtc && faceValue) {
      const otc = filtered
        .filter((t) => t.segment === "otc" && (t.wap ?? t.last_price) != null)
        .map((t) => ({
          time: ts(t.trade_date),
          value: (Number(t.wap ?? t.last_price) / 100) * faceValue,
        }))
        .sort((a, b) => (a.time as number) - (b.time as number))
        // lightweight-charts rejects duplicate timestamps
        .filter((p, i, arr) => i === 0 || p.time !== arr[i - 1].time);
      if (otc.length) {
        const line = chart.addSeries(LineSeries, {
          color: "#a1a1aa",
          lineWidth: 1,
          lineStyle: 1,
          priceLineVisible: false,
        });
        line.setData(otc);
        otcSeries = line;
      }
    }

    // Crosshair tooltip: date + retail ₹ / volume / OTC ₹ at the cursor.
    chart.subscribeCrosshairMove((param) => {
      const el = tooltip.current;
      if (!el) return;
      if (!param.time || !param.point) {
        el.style.display = "none";
        return;
      }
      const rows: string[] = [];
      const rv = retailSeries && param.seriesData.get(retailSeries);
      const vv = volSeries && param.seriesData.get(volSeries);
      const ov = otcSeries && param.seriesData.get(otcSeries);
      if (rv && "value" in rv)
        rows.push(
          `<span style="color:#10b981">Retail</span> ₹${Number(rv.value).toLocaleString("en-IN")}`,
        );
      if (vv && "value" in vv && vv.value > 0)
        rows.push(
          `<span style="color:#71717a">Vol</span> ${Number(vv.value).toLocaleString("en-IN")}`,
        );
      if (ov && "value" in ov)
        rows.push(
          `<span style="color:#a1a1aa">OTC</span> ₹${Number(ov.value).toLocaleString("en-IN")}`,
        );
      if (rows.length === 0) {
        el.style.display = "none";
        return;
      }
      const d = new Date((param.time as number) * 1000);
      el.innerHTML = `<div style="color:#71717a;margin-bottom:2px">${d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}</div>${rows.join("<br/>")}`;
      el.style.display = "block";
      const w = container.current?.clientWidth ?? 0;
      const flip = param.point.x > w - 150;
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
      chartRef.current = null;
    };
  }, [filtered, showOtc, faceValue]);

  const stats = useMemo(() => {
    const retail = filtered.filter((t) => t.segment === "retail");
    const otc = filtered.filter((t) => t.segment === "otc");
    const closes = retail.map((t) => Number(t.close)).filter((v) => v > 0);
    const sum = (xs: (number | null)[]) => xs.reduce((a: number, v) => a + (Number(v) || 0), 0);
    return {
      retailDays: retail.length,
      otcDays: otc.length,
      high: closes.length ? Math.max(...closes) : null,
      low: closes.length ? Math.min(...closes) : null,
      retailTurnover: sum(retail.map((t) => t.turnover)),
      retailVolume: sum(retail.map((t) => t.volume)),
      otcTurnover: sum(otc.map((t) => t.turnover)),
      trades: sum(retail.map((t) => t.num_trades)),
    };
  }, [filtered]);

  const [showAll, setShowAll] = useState(false);
  const sortedTrades = useMemo(
    () =>
      [...filtered].sort(
        (a, b) => b.trade_date.localeCompare(a.trade_date) || (a.segment < b.segment ? -1 : 1),
      ),
    [filtered],
  );
  const recent = showAll ? sortedTrades : sortedTrades.slice(0, 14);
  // A huge "All"-range list shouldn't stick around when filters change.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset on filter change is the point
  useEffect(() => setShowAll(false), [range, exchange]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {RANGES.map(([r]) => (
          <button
            type="button"
            key={r}
            onClick={() => setRange(r)}
            className={
              "px-2.5 py-1 rounded-md border " +
              (range === r
                ? "bg-emerald-600 border-emerald-600 text-white"
                : "border-zinc-700 text-zinc-400 hover:text-zinc-200")
            }
          >
            {r}
          </button>
        ))}
        <span className="w-px h-4 bg-zinc-800 mx-1" />
        {(["", "NSE", "BSE"] as const).map((ex) => (
          <button
            type="button"
            key={ex || "both"}
            onClick={() => setExchange(ex)}
            className={
              "px-2.5 py-1 rounded-md border " +
              (exchange === ex
                ? "bg-zinc-700 border-zinc-600 text-white"
                : "border-zinc-700 text-zinc-400 hover:text-zinc-200")
            }
          >
            {ex || "Both"}
          </button>
        ))}
        <label className="flex items-center gap-1.5 ml-auto text-zinc-400 cursor-pointer">
          <input
            type="checkbox"
            className="accent-emerald-500"
            checked={showOtc}
            onChange={(e) => setShowOtc(e.target.checked)}
          />
          OTC overlay
        </label>
      </div>

      {isLoading ? (
        <div className="h-[340px] grid place-items-center text-zinc-500 text-sm animate-pulse">
          Loading trades…
        </div>
      ) : filtered.length === 0 ? (
        <div className="h-[340px] grid place-items-center text-zinc-500 text-sm">
          No trades in this range.
        </div>
      ) : (
        <div className="relative">
          <div ref={container} className="w-full" />
          <div
            ref={tooltip}
            className="pointer-events-none absolute z-20 hidden rounded-md border border-zinc-700 bg-zinc-900/95 px-2.5 py-1.5 text-[11px] leading-snug text-zinc-200 shadow-lg"
          />
        </div>
      )}

      <div className="text-xs text-zinc-600">
        <span className="text-emerald-500">— retail dirty ₹</span> ·{" "}
        <span className="text-zinc-400">-- OTC clean % × face</span>
      </div>

      {filtered.length > 0 && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {(
              [
                ["Period high", stats.high != null ? fmt.inr(stats.high) : "–"],
                ["Period low", stats.low != null ? fmt.inr(stats.low) : "–"],
                ["Retail turnover", `${fmt.inr(stats.retailTurnover)} / ${stats.retailDays}d`],
                [
                  "OTC turnover",
                  stats.otcDays ? `${fmt.inr(stats.otcTurnover)} / ${stats.otcDays}d` : "–",
                ],
                ["Units traded (retail)", stats.retailVolume.toLocaleString("en-IN")],
                ["Trades (retail)", stats.trades ? stats.trades.toLocaleString("en-IN") : "–"],
                ["Face value", faceValue != null ? fmt.inrFull(faceValue) : "–"],
              ] as const
            ).map(([label, value]) => (
              <StatBox key={label} label={label} value={value} />
            ))}
          </div>

          <div>
            <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">
              Recent trades
            </h3>
            <div
              className={`rounded-lg border border-zinc-800 overflow-x-auto ${
                showAll ? "max-h-96 overflow-y-auto scroll-slim" : ""
              }`}
            >
              <table className="w-full text-xs">
                <thead className={showAll ? "sticky top-0 bg-zinc-950 z-10" : ""}>
                  <tr className="text-left text-zinc-500 border-b border-zinc-800">
                    <th className="px-3 py-1.5 font-medium">Date</th>
                    <th className="px-3 py-1.5 font-medium">Segment</th>
                    <th className="px-3 py-1.5 font-medium">Price</th>
                    <th className="px-3 py-1.5 font-medium">Yield</th>
                    <th className="px-3 py-1.5 font-medium">Volume</th>
                    <th className="px-3 py-1.5 font-medium">Turnover</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.map((t) => (
                    <tr
                      key={`${t.trade_date}-${t.exchange}-${t.segment}`}
                      className="border-b border-zinc-800/50 last:border-0"
                    >
                      <td className="px-3 py-1.5 text-zinc-400 whitespace-nowrap">
                        {fmt.date(t.trade_date)}
                      </td>
                      <td className="px-3 py-1.5">
                        <span
                          className={t.segment === "retail" ? "text-emerald-500" : "text-zinc-400"}
                        >
                          {t.segment === "retail"
                            ? `Retail (${t.exchange})`
                            : `OTC (${t.exchange})`}
                        </span>
                      </td>
                      <td className="px-3 py-1.5 text-zinc-200 whitespace-nowrap">
                        {t.segment === "retail" ? (
                          fmt.inr(t.close != null ? Number(t.close) : null)
                        ) : (t.wap ?? t.last_price) != null ? (
                          faceValue != null ? (
                            <div title="Clean price (% of face × face value) — excludes accrued interest, unlike retail dirty ₹">
                              <div>
                                {fmt.inrFull((Number(t.wap ?? t.last_price) / 100) * faceValue)}
                              </div>
                              <div className="text-[10px] text-zinc-500">
                                {fmt.num(Number(t.wap ?? t.last_price))}%
                              </div>
                            </div>
                          ) : (
                            `${fmt.num(Number(t.wap ?? t.last_price))}%`
                          )
                        ) : (
                          "–"
                        )}
                      </td>
                      <td className="px-3 py-1.5 text-zinc-400">
                        {fmt.pct(
                          t.wap_yield != null || t.last_yield != null
                            ? Number(t.wap_yield ?? t.last_yield)
                            : null,
                        )}
                      </td>
                      <td className="px-3 py-1.5 text-zinc-400">
                        {t.volume != null ? Number(t.volume).toLocaleString("en-IN") : "–"}
                      </td>
                      <td className="px-3 py-1.5 text-zinc-400">
                        {fmt.inr(t.turnover != null ? Number(t.turnover) : null)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {sortedTrades.length > 14 && (
              <button
                type="button"
                onClick={() => setShowAll(!showAll)}
                className="mt-2 text-xs text-emerald-400 hover:text-emerald-300"
              >
                {showAll
                  ? "Show less ▴"
                  : `Show all (${sortedTrades.length.toLocaleString("en-IN")}) ▾`}
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
