import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ScreenerParams } from "../api/types";

interface Preset {
  name: string;
  hint: string;
  params: ScreenerParams;
}

const PRESETS: Preset[] = [
  { name: "High yield", hint: "YTM ≥ 10%", params: { min_ytm: 10, fresh_only: true } },
  { name: "Quality", hint: "≥ AA-, exact", params: { min_rating: "AA-", exact_only: true } },
  { name: "Short term", hint: "≤ 2y, fresh", params: { max_years: 2, fresh_only: true } },
  { name: "Below par", hint: "price < face", params: { below_par: true, fresh_only: true } },
  { name: "Monthly income", hint: "monthly coupons", params: { frequency: "monthly", fresh_only: true } },
  { name: "Downgrades", hint: "last 30d", params: { downgraded_only: true } },
  { name: "Watchlist", hint: "starred", params: { watchlist_only: true } },
];

const inputCls =
  "bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1.5 text-sm w-full " +
  "focus:outline-none focus:border-emerald-500 placeholder:text-zinc-600";

export default function FilterBar({
  params,
  onChange,
}: {
  params: ScreenerParams;
  onChange: (p: ScreenerParams) => void;
}) {
  const [open, setOpen] = useState(false);
  const [activePreset, setActivePreset] = useState<string | null>(null);
  const { data: filters } = useQuery({ queryKey: ["filters"], queryFn: api.filters });

  const set = (patch: Partial<ScreenerParams>) => {
    setActivePreset(null);
    onChange({ ...params, ...patch });
  };

  const applyPreset = (p: Preset) => {
    if (activePreset === p.name) {
      setActivePreset(null);
      onChange({});
    } else {
      setActivePreset(p.name);
      onChange({ ...p.params, q: params.q });
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="text"
          placeholder="Search ISIN / symbol / issuer…"
          className={`${inputCls} max-w-xs`}
          value={params.q ?? ""}
          onChange={(e) => set({ q: e.target.value || undefined })}
        />
        {PRESETS.map((p) => (
          <button
            key={p.name}
            onClick={() => applyPreset(p)}
            className={
              "text-xs px-3 py-1.5 rounded-full border transition-colors " +
              (activePreset === p.name
                ? "bg-emerald-500 border-emerald-500 text-zinc-950 font-semibold"
                : "border-zinc-700 text-zinc-400 hover:border-emerald-400 hover:text-emerald-400")
            }
          >
            {p.name}
            <span className="ml-1.5 pl-1.5 border-l border-zinc-700/50 opacity-70">{p.hint}</span>
          </button>
        ))}
        <button
          onClick={() => setOpen(!open)}
          className="text-xs px-3 py-1.5 rounded-full border border-zinc-700 text-zinc-400 hover:text-zinc-200 ml-auto"
        >
          {open ? "Hide filters" : "More filters"}
        </button>
      </div>

      {open && (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 p-4 rounded-lg border border-zinc-800 bg-zinc-900/50">
          <label className="text-xs text-zinc-400 space-y-1">
            <span>Min YTM %</span>
            <input type="number" className={inputCls} value={params.min_ytm ?? ""}
              onChange={(e) => set({ min_ytm: e.target.value ? +e.target.value : undefined })} />
          </label>
          <label className="text-xs text-zinc-400 space-y-1">
            <span>Max YTM %</span>
            <input type="number" className={inputCls} value={params.max_ytm ?? ""}
              onChange={(e) => set({ max_ytm: e.target.value ? +e.target.value : undefined })} />
          </label>
          <label className="text-xs text-zinc-400 space-y-1">
            <span>Min rating</span>
            <select className={inputCls} value={params.min_rating ?? ""}
              onChange={(e) => set({ min_rating: e.target.value || undefined })}>
              <option value="">Any</option>
              {(filters?.ratings ?? []).map((r) => <option key={r}>{r}</option>)}
            </select>
          </label>
          <label className="text-xs text-zinc-400 space-y-1">
            <span>Sector</span>
            <select className={inputCls} value={params.sector ?? ""}
              onChange={(e) => set({ sector: e.target.value || undefined })}>
              <option value="">Any</option>
              {(filters?.sectors ?? []).map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          <label className="text-xs text-zinc-400 space-y-1">
            <span>Coupon frequency</span>
            <select className={inputCls} value={params.frequency ?? ""}
              onChange={(e) => set({ frequency: e.target.value || undefined })}>
              <option value="">Any</option>
              {(filters?.frequencies ?? []).map((f) => <option key={f}>{f}</option>)}
            </select>
          </label>
          <label className="text-xs text-zinc-400 space-y-1">
            <span>Exchange</span>
            <select className={inputCls} value={params.exchange ?? ""}
              onChange={(e) => set({ exchange: e.target.value || undefined })}>
              <option value="">Any</option>
              <option>NSE</option>
              <option>BSE</option>
            </select>
          </label>
          <label className="text-xs text-zinc-400 space-y-1">
            <span>Min years</span>
            <input type="number" className={inputCls} value={params.min_years ?? ""}
              onChange={(e) => set({ min_years: e.target.value ? +e.target.value : undefined })} />
          </label>
          <label className="text-xs text-zinc-400 space-y-1">
            <span>Max years</span>
            <input type="number" className={inputCls} value={params.max_years ?? ""}
              onChange={(e) => set({ max_years: e.target.value ? +e.target.value : undefined })} />
          </label>
          <label className="text-xs text-zinc-400 space-y-1">
            <span>Max price ₹</span>
            <input type="number" className={inputCls} value={params.max_ltp ?? ""}
              onChange={(e) => set({ max_ltp: e.target.value ? +e.target.value : undefined })} />
          </label>
          <label className="text-xs text-zinc-400 space-y-1">
            <span>Min turnover ₹</span>
            <input type="number" className={inputCls} value={params.min_turnover ?? ""}
              onChange={(e) => set({ min_turnover: e.target.value ? +e.target.value : undefined })} />
          </label>
          <div className="col-span-2 flex flex-wrap items-end gap-3 text-xs text-zinc-400">
            {(
              [
                ["fresh_only", "Fresh prices"],
                ["exact_only", "Exact YTM"],
                ["secured_only", "Secured"],
                ["tax_free_only", "Tax-free"],
                ["below_par", "Below par"],
              ] as const
            ).map(([key, label]) => (
              <label key={key} className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  className="accent-emerald-500"
                  checked={!!params[key]}
                  onChange={(e) => set({ [key]: e.target.checked || undefined })}
                />
                {label}
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
