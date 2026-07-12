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
import { api } from "../api/client";

const RANGES = [
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
  const chartRef = useRef<IChartApi | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["trades", isin],
    queryFn: () => api.trades(isin),
  });

  const filtered = useMemo(() => {
    if (!data) return [];
    const days = RANGES.find(([r]) => r === range)![1];
    const cutoff = days ? Date.now() - days * 86400_000 : 0;
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
        title: "Retail ₹",
      });
      area.setData(retail.map((r) => ({ time: r.time, value: r.close })));

      const vol = chart.addSeries(HistogramSeries, {
        priceScaleId: "vol",
        color: "rgba(113,113,122,0.5)",
        priceFormat: { type: "volume" },
      });
      chart.priceScale("vol").applyOptions({
        scaleMargins: { top: 0.82, bottom: 0 },
      });
      vol.setData(retail.map((r) => ({ time: r.time, value: r.volume })));
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
          title: "OTC clean→₹",
        });
        line.setData(otc);
      }
    }

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

  const counts = useMemo(() => {
    const retail = filtered.filter((t) => t.segment === "retail").length;
    return { retail, otc: filtered.length - retail };
  }, [filtered]);

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
        <div ref={container} className="w-full" />
      )}

      <div className="text-xs text-zinc-600">
        {counts.retail} retail days · {counts.otc} OTC days ·{" "}
        <span className="text-emerald-500">— retail dirty ₹</span> ·{" "}
        <span className="text-zinc-400">-- OTC clean % × face</span>
      </div>
    </div>
  );
}
