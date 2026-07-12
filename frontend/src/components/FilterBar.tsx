import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, fmt } from "../api/client";
import type { ScreenerParams } from "../api/types";
import Tip from "./Tooltip";

export interface Preset {
  id: string;
  name: string;
  sub?: string;
  tip: string;
  params: ScreenerParams;
}

// The v1 strategy presets, mapped onto v2 screener params.
export const PRESETS: Preset[] = [
  {
    id: "ultrasafe",
    name: "Ultra Safe",
    sub: "AAA ₹5L+",
    tip: "The safest bonds there are — and easy to buy and sell",
    params: { min_rating: "AAA", min_turnover: 500000 },
  },
  {
    id: "fdbeater",
    name: "FD Beater",
    sub: "≥8% AA-",
    tip: "Earn more than a bank FD, with strong safety ratings",
    params: { min_rating: "AA-", min_ytm: 8, min_turnover: 100000 },
  },
  {
    id: "sweet",
    name: "Sweet Spot",
    sub: "≥9.5% A- ≤4y",
    tip: "High returns at reasonable risk, without locking money away for years",
    params: { min_rating: "A-", min_ytm: 9.5, max_years: 4, min_turnover: 100000 },
  },
  {
    id: "parking",
    name: "Cash Parking",
    sub: "≤1.5y ≥7.5%",
    tip: "For money you'll want back soon",
    params: { min_rating: "AA-", min_ytm: 7.5, max_years: 1.5 },
  },
  {
    id: "highyield",
    name: "High Yield",
    sub: "≥10.5% BBB-",
    tip: "Maximum returns while staying out of junk territory",
    params: { min_rating: "BBB-", min_ytm: 10.5 },
  },
  {
    id: "lockin",
    name: "Rate Lock-in",
    sub: "≥5y ≥8.5%",
    tip: "Lock in today's rates for 5+ years — smart if you expect rates to fall",
    params: { min_rating: "AA-", min_ytm: 8.5, min_years: 5 },
  },
  {
    id: "maturing",
    name: "Maturing Soon",
    sub: "≤1y",
    tip: "Repays your money within a year",
    params: { min_rating: "AA-", max_years: 1 },
  },
  {
    id: "monthly",
    name: "Monthly Income",
    sub: "≥9% A-",
    tip: "Pays you interest every month, like rent",
    params: { min_rating: "A-", min_ytm: 9, frequency: "monthly" },
  },
  {
    id: "value",
    name: "Value Hunter",
    sub: "≥9% AA- exact",
    tip: "Well-paid credit risk on numbers you can trust: strong rating, exact NSDL schedule, fresh price",
    params: { min_rating: "AA-", min_ytm: 9, exact_only: true, fresh_only: true },
  },
  {
    id: "discount",
    name: "Discount Bonds",
    sub: "< face",
    tip: "Trading below face value — part of your return comes as a guaranteed gain at redemption",
    params: { below_par: true },
  },
  {
    id: "downgraded",
    name: "Downgraded",
    sub: "≤30d",
    tip: "Rating cut in the last 30 days — credits to avoid, or overreactions to hunt",
    params: { downgraded_only: true },
  },
  {
    id: "liquid",
    name: "Most Traded",
    sub: "₹10L+",
    tip: "Where the most buying and selling is happening today",
    params: { min_turnover: 1000000 },
  },
  {
    id: "watchlist",
    name: "★ Watchlist",
    tip: "Your starred bonds",
    params: { watchlist_only: true },
  },
];

export const DEFAULT_PRESET = PRESETS.find((p) => p.id === "fdbeater")!;

/** v1's describeFilters(): human-readable summary of active filters. */
export function describeFilters(p: ScreenerParams): string[] {
  const parts: string[] = [];
  if (p.min_rating) parts.push(`rating ≥ ${p.min_rating}`);
  if (p.min_ytm != null) parts.push(`YTM ≥ ${p.min_ytm}%`);
  if (p.max_ytm != null) parts.push(`YTM ≤ ${p.max_ytm}%`);
  if (p.min_years != null) parts.push(`maturity ≥ ${p.min_years}y`);
  if (p.max_years != null) parts.push(`maturity ≤ ${p.max_years}y`);
  if (p.max_ltp != null) parts.push(`price ≤ ₹${p.max_ltp.toLocaleString("en-IN")}`);
  if (p.min_turnover != null) parts.push(`retail traded ≥ ${fmt.inr(p.min_turnover)}`);
  if (p.min_otc_turnover != null) parts.push(`OTC traded ≥ ${fmt.inr(p.min_otc_turnover)}`);
  if (p.frequency) parts.push(`pays ${p.frequency}`);
  if (p.sector) parts.push(`sector: ${p.sector}`);
  if (p.exchange) parts.push(`on ${p.exchange}`);
  if (p.watchlist_only) parts.push("watchlist only");
  if (p.fresh_only) parts.push("fresh prices");
  if (p.exact_only) parts.push("exact YTM only");
  if (p.below_par) parts.push("below face value");
  if (p.downgraded_only) parts.push("downgraded recently");
  if (p.secured_only) parts.push("secured only");
  if (p.tax_free_only) parts.push("tax-free only");
  return parts;
}

const inputCls =
  "w-full bg-transparent border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs font-medium " +
  "focus:outline-none focus:border-emerald-500 placeholder:text-zinc-600";

const labelCls = "block text-[10px] font-semibold uppercase tracking-wider text-zinc-500 mb-1.5";

export default function FilterBar({
  params,
  onChange,
}: {
  params: ScreenerParams;
  onChange: (p: ScreenerParams) => void;
}) {
  const [open, setOpen] = useState(false);
  const [activePreset, setActivePreset] = useState<string | null>(DEFAULT_PRESET.id);
  const { data: filters } = useQuery({ queryKey: ["filters"], queryFn: api.filters });

  const set = (patch: Partial<ScreenerParams>) => {
    setActivePreset(null);
    onChange({ ...params, ...patch });
  };

  const applyPreset = (p: Preset) => {
    if (activePreset === p.id) {
      setActivePreset(null);
      onChange({ q: params.q });
    } else {
      setActivePreset(p.id);
      onChange({ ...p.params, q: params.q });
    }
  };

  const summary = describeFilters(params);

  return (
    <div className="space-y-5">
      <section className="pt-4">
        <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-7 gap-2">
          {PRESETS.map((p) => (
            <Tip key={p.id} text={p.tip} className="contents">
              <button
                type="button"
                onClick={() => applyPreset(p)}
                className={`preset-chip ${activePreset === p.id ? "active" : ""}`}
              >
                {p.name}
                {p.sub && <span>{p.sub}</span>}
              </button>
            </Tip>
          ))}
          <Tip text="Show everything" className="contents">
            <button
              type="button"
              onClick={() => {
                setActivePreset(null);
                onChange({ q: params.q });
              }}
              className="preset-chip chip-clear"
            >
              Clear
            </button>
          </Tip>
        </div>
      </section>

      <section className="rounded-lg border border-zinc-800 bg-zinc-900/50">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="w-full flex items-center justify-between gap-4 px-3.5 py-2.5 text-left"
        >
          <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
            <span className={`inline-block mr-1.5 transition-transform ${open ? "rotate-90" : ""}`}>
              ›
            </span>
            Filters{summary.length > 0 && ` · ${summary.length} active`}
          </span>
          {!open && summary.length > 0 && (
            <span className="text-xs text-emerald-400/90 truncate">
              {summary.map((s, i) => (
                <span key={s}>
                  {i > 0 && <span className="text-zinc-600 mx-1.5">|</span>}
                  {s}
                </span>
              ))}
            </span>
          )}
        </button>

        {open && (
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 px-3.5 pb-4 pt-1">
            <label className="text-xs text-zinc-400">
              <span className={labelCls}>Min YTM %</span>
              <input
                type="number"
                className={inputCls}
                value={params.min_ytm ?? ""}
                onChange={(e) => set({ min_ytm: e.target.value ? +e.target.value : undefined })}
              />
            </label>
            <label className="text-xs text-zinc-400">
              <span className={labelCls}>Max YTM %</span>
              <input
                type="number"
                className={inputCls}
                value={params.max_ytm ?? ""}
                onChange={(e) => set({ max_ytm: e.target.value ? +e.target.value : undefined })}
              />
            </label>
            <label className="text-xs text-zinc-400">
              <span className={labelCls}>Min rating</span>
              <select
                className={inputCls}
                value={params.min_rating ?? ""}
                onChange={(e) => set({ min_rating: e.target.value || undefined })}
              >
                <option value="">Any</option>
                {(filters?.ratings ?? []).map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
            </label>
            <label className="text-xs text-zinc-400">
              <span className={labelCls}>Sector</span>
              <select
                className={inputCls}
                value={params.sector ?? ""}
                onChange={(e) => set({ sector: e.target.value || undefined })}
              >
                <option value="">Any</option>
                {(filters?.sectors ?? []).map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </label>
            <label className="text-xs text-zinc-400">
              <span className={labelCls}>Pays interest</span>
              <select
                className={inputCls}
                value={params.frequency ?? ""}
                onChange={(e) => set({ frequency: e.target.value || undefined })}
              >
                <option value="">Any</option>
                {(filters?.frequencies ?? []).map((f) => (
                  <option key={f}>{f}</option>
                ))}
              </select>
            </label>
            <label className="text-xs text-zinc-400">
              <span className={labelCls}>Exchange</span>
              <select
                className={inputCls}
                value={params.exchange ?? ""}
                onChange={(e) => set({ exchange: e.target.value || undefined })}
              >
                <option value="">Any</option>
                <option>NSE</option>
                <option>BSE</option>
              </select>
            </label>
            <label className="text-xs text-zinc-400">
              <span className={labelCls}>Min maturity (years)</span>
              <input
                type="number"
                className={inputCls}
                value={params.min_years ?? ""}
                onChange={(e) => set({ min_years: e.target.value ? +e.target.value : undefined })}
              />
            </label>
            <label className="text-xs text-zinc-400">
              <span className={labelCls}>Max maturity (years)</span>
              <input
                type="number"
                className={inputCls}
                value={params.max_years ?? ""}
                onChange={(e) => set({ max_years: e.target.value ? +e.target.value : undefined })}
              />
            </label>
            <label className="text-xs text-zinc-400">
              <span className={labelCls}>Ticket size (max ₹/unit)</span>
              <input
                type="number"
                className={inputCls}
                value={params.max_ltp ?? ""}
                onChange={(e) => set({ max_ltp: e.target.value ? +e.target.value : undefined })}
              />
            </label>
            <label className="text-xs text-zinc-400">
              <span className={labelCls}>Retail traded ₹ (min)</span>
              <input
                type="number"
                className={inputCls}
                value={params.min_turnover ?? ""}
                onChange={(e) =>
                  set({ min_turnover: e.target.value ? +e.target.value : undefined })
                }
              />
            </label>
            <label className="text-xs text-zinc-400">
              <span className={labelCls}>OTC traded ₹ (min)</span>
              <input
                type="number"
                className={inputCls}
                value={params.min_otc_turnover ?? ""}
                onChange={(e) =>
                  set({ min_otc_turnover: e.target.value ? +e.target.value : undefined })
                }
              />
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
      </section>
    </div>
  );
}
