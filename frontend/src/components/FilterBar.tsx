import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, fmt } from "../api/client";
import type { Filters, ScreenerParams } from "../api/types";
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
    tip: "Well-paid credit risk on numbers you can trust: strong rating, exact NSDL schedule, traded within a week",
    params: { min_rating: "AA-", min_ytm: 9, exact_only: true, traded_within: 7 },
  },
  {
    id: "taxfree",
    name: "Tax-Free",
    tip: "Interest exempt from income tax — compare after-tax, not headline yield",
    params: { tax_free_only: true },
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

/* Chips shown inline; the rest live under "More ▾". */
const INLINE_IDS = ["fdbeater", "ultrasafe", "sweet", "highyield", "liquid", "watchlist"];

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
  if (p.retail_only) parts.push("trades retail");
  if (p.otc_only) parts.push("trades OTC");
  if (p.watchlist_only) parts.push("watchlist only");
  if (p.fresh_only) parts.push("fresh prices");
  if (p.traded_within != null) parts.push(`traded ≤ ${p.traded_within}d ago`);
  if (p.unsecured_only) parts.push("unsecured only");
  if (p.exact_only) parts.push("exact YTM only");
  if (p.below_par) parts.push("below face value");
  if (p.downgraded_only) parts.push("downgraded recently");
  if (p.secured_only) parts.push("secured only");
  if (p.tax_free_only) parts.push("tax-free only");
  return parts;
}

export default function FilterBar({
  params,
  onChange,
}: {
  params: ScreenerParams;
  onChange: (p: ScreenerParams) => void;
}) {
  const [moreOpen, setMoreOpen] = useState(false);
  const [activePreset, setActivePreset] = useState<string | null>(DEFAULT_PRESET.id);
  const { data: filters } = useQuery({ queryKey: ["filters"], queryFn: api.filters });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMoreOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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

  const inline = INLINE_IDS.map((id) => PRESETS.find((p) => p.id === id)!);
  const overflow = PRESETS.filter((p) => !INLINE_IDS.includes(p.id));
  const activeOverflow = overflow.find((p) => p.id === activePreset);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {inline.map((p) => (
          <Tip key={p.id} text={p.tip} className="contents">
            <button
              type="button"
              onClick={() => applyPreset(p)}
              className={`preset-chip flex-1 ${activePreset === p.id ? "active" : ""}`}
            >
              {p.name}
              {p.sub && <span>{p.sub}</span>}
            </button>
          </Tip>
        ))}

        <span className="relative flex-1 flex">
          <button
            type="button"
            onClick={() => setMoreOpen(!moreOpen)}
            className={`preset-chip flex-1 ${activeOverflow ? "active" : ""}`}
          >
            {activeOverflow ? `More: ${activeOverflow.name}` : "More"}
            <span>▾</span>
          </button>
          {moreOpen && (
            <>
              <div
                className="fixed inset-0 z-20"
                onClick={() => setMoreOpen(false)}
                aria-hidden="true"
              />
              <div className="absolute left-0 top-full mt-2 z-30 w-64 rounded-lg border border-zinc-700 bg-zinc-900 shadow-xl py-1">
                {overflow.map((p) => (
                  <button
                    type="button"
                    key={p.id}
                    title={p.tip}
                    onClick={() => {
                      applyPreset(p);
                      setMoreOpen(false);
                    }}
                    className={`w-full flex items-center justify-between gap-3 px-3 py-2 text-xs text-left hover:bg-zinc-800 ${
                      activePreset === p.id ? "text-emerald-400" : "text-zinc-300"
                    }`}
                  >
                    <span className="font-medium">{p.name}</span>
                    {p.sub && <span className="text-[10px] text-zinc-500">{p.sub}</span>}
                  </button>
                ))}
              </div>
            </>
          )}
        </span>

        <Tip text="Show everything" className="contents">
          <button
            type="button"
            onClick={() => {
              setActivePreset(null);
              onChange({ q: params.q });
            }}
            className="preset-chip chip-clear flex-1"
          >
            Clear
          </button>
        </Tip>
      </div>

      <FilterFields params={params} set={set} filters={filters} />
    </div>
  );
}

type NumKey =
  | "min_ytm"
  | "max_ytm"
  | "min_years"
  | "max_years"
  | "max_ltp"
  | "min_turnover"
  | "min_otc_turnover"
  | "traded_within";
type SelKey = "min_rating" | "sector" | "frequency" | "exchange";

/* One always-visible row. Fields with a value are highlighted in emerald. */
function FilterFields({
  params,
  set,
  filters,
}: {
  params: ScreenerParams;
  set: (patch: Partial<ScreenerParams>) => void;
  filters: Filters | undefined;
}) {
  const lbl = (active: boolean) =>
    `block text-[10px] font-semibold uppercase tracking-wider mb-1.5 whitespace-nowrap ${
      active ? "text-emerald-600" : "text-zinc-500"
    }`;
  const box = (active: boolean) =>
    "w-full bg-transparent border rounded-md px-2 py-1.5 text-xs font-medium " +
    "focus:outline-none focus:border-emerald-600 placeholder:text-zinc-600 " +
    (active ? "border-emerald-700/70" : "border-zinc-700");

  const num = (label: string, key: NumKey) => {
    const active = params[key] != null;
    return (
      <label>
        <span className={lbl(active)}>{label}</span>
        <input
          type="number"
          className={box(active)}
          value={params[key] ?? ""}
          onChange={(e) =>
            set({ [key]: e.target.value ? +e.target.value : undefined } as Partial<ScreenerParams>)
          }
        />
      </label>
    );
  };

  const sel = (label: string, key: SelKey, options: string[]) => {
    const active = !!params[key];
    return (
      <label>
        <span className={lbl(active)}>{label}</span>
        <select
          className={box(active)}
          value={params[key] ?? ""}
          onChange={(e) => set({ [key]: e.target.value || undefined } as Partial<ScreenerParams>)}
        >
          <option value="">Any</option>
          {options.map((o) => (
            <option key={o}>{o}</option>
          ))}
        </select>
      </label>
    );
  };

  // Only the essentials show by default; every other filter lives behind
  // "+ Filter" and appears once added (or whenever a preset sets its params).
  // Removing one clears its params.
  const EXTRAS = [
    ["max_ytm", "Max YTM %"],
    ["sector", "Sector"],
    ["frequency", "Pays"],
    ["exchange", "Exchange"],
    ["max_years", "Max maturity"],
    ["max_ltp", "Ticket size"],
    ["min_otc_turnover", "OTC turnover"],
    ["segment", "Segment"],
    ["security", "Security"],
    ["ytm_basis", "YTM basis"],
  ] as const;
  type ExtraId = (typeof EXTRAS)[number][0];

  const extraActive: Record<ExtraId, boolean> = {
    max_ytm: params.max_ytm != null,
    sector: !!params.sector,
    frequency: !!params.frequency,
    exchange: !!params.exchange,
    max_years: params.max_years != null,
    max_ltp: params.max_ltp != null,
    min_otc_turnover: params.min_otc_turnover != null,
    segment: !!params.retail_only || !!params.otc_only,
    security: !!params.secured_only || !!params.unsecured_only,
    ytm_basis: !!params.exact_only,
  };
  const [added, setAdded] = useState<ExtraId[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const visible = EXTRAS.filter(([id]) => added.includes(id) || extraActive[id]);

  const clearExtra: Record<ExtraId, Partial<ScreenerParams>> = {
    max_ytm: { max_ytm: undefined },
    sector: { sector: undefined },
    frequency: { frequency: undefined },
    exchange: { exchange: undefined },
    max_years: { max_years: undefined },
    max_ltp: { max_ltp: undefined },
    min_otc_turnover: { min_otc_turnover: undefined },
    segment: { retail_only: undefined, otc_only: undefined },
    security: { secured_only: undefined, unsecured_only: undefined },
    ytm_basis: { exact_only: undefined },
  };

  const extraControl = (id: ExtraId) => {
    switch (id) {
      case "segment": {
        const v =
          params.retail_only && params.otc_only
            ? "both"
            : params.retail_only
              ? "retail"
              : params.otc_only
                ? "otc"
                : "";
        return (
          <label className="flex-1">
            <span className={lbl(!!v)}>Segment</span>
            <select
              className={box(!!v)}
              value={v}
              onChange={(e) =>
                set({
                  retail_only: ["retail", "both"].includes(e.target.value) || undefined,
                  otc_only: ["otc", "both"].includes(e.target.value) || undefined,
                })
              }
            >
              <option value="">Any</option>
              <option value="retail">Trades retail</option>
              <option value="otc">Trades OTC</option>
              <option value="both">Retail + OTC</option>
            </select>
          </label>
        );
      }
      case "max_ytm":
        return num("Max YTM %", "max_ytm");
      case "sector":
        return sel("Sector", "sector", filters?.sectors ?? []);
      case "frequency":
        return sel("Pays", "frequency", filters?.frequencies ?? []);
      case "exchange":
        return sel("Exchange", "exchange", ["NSE", "BSE"]);
      case "max_years":
        return num("Max mat (y)", "max_years");
      case "max_ltp":
        return num("Ticket ₹ max", "max_ltp");
      case "min_otc_turnover":
        return num("OTC trd ₹", "min_otc_turnover");
      case "security": {
        const v = params.secured_only ? "secured" : params.unsecured_only ? "unsecured" : "";
        return (
          <label className="flex-1">
            <span className={lbl(!!v)}>Security</span>
            <select
              className={box(!!v)}
              value={v}
              onChange={(e) =>
                set({
                  secured_only: e.target.value === "secured" || undefined,
                  unsecured_only: e.target.value === "unsecured" || undefined,
                })
              }
            >
              <option value="">Any</option>
              <option value="secured">Secured</option>
              <option value="unsecured">Unsecured</option>
            </select>
          </label>
        );
      }
      case "ytm_basis":
        return (
          <label className="flex-1">
            <span className={lbl(!!params.exact_only)}>YTM basis</span>
            <select
              className={box(!!params.exact_only)}
              value={params.exact_only ? "exact" : ""}
              onChange={(e) => set({ exact_only: e.target.value === "exact" || undefined })}
            >
              <option value="">Any</option>
              <option value="exact">Exact only</option>
            </select>
          </label>
        );
    }
  };

  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] items-end gap-3">
      {num("Min YTM %", "min_ytm")}
      {num("Retail trd ₹", "min_turnover")}
      {sel("Min rating", "min_rating", filters?.ratings ?? [])}
      {num("Min mat (y)", "min_years")}
      {num("Last traded ≤ (d)", "traded_within")}

      {visible.map(([id, label]) => (
        <span key={id} className="flex items-end gap-1">
          {extraControl(id)}
          <button
            type="button"
            title={`Remove ${label} filter`}
            onClick={() => {
              setAdded((a) => a.filter((x) => x !== id));
              set(clearExtra[id]);
            }}
            className="pb-2 text-zinc-600 hover:text-red-400 text-xs leading-none"
          >
            ×
          </button>
        </span>
      ))}

      <span className="relative self-end justify-self-start">
        <button
          type="button"
          onClick={() => setAddOpen(!addOpen)}
          className="px-2.5 py-1.5 rounded-md border border-dashed border-zinc-700 text-xs font-medium text-zinc-500 hover:text-zinc-300 hover:border-zinc-500 transition-colors whitespace-nowrap"
        >
          + Filter
        </button>
        {addOpen && (
          <>
            <div
              className="fixed inset-0 z-20"
              onClick={() => setAddOpen(false)}
              aria-hidden="true"
            />
            <div className="absolute right-0 bottom-full mb-2 z-30 w-44 rounded-lg border border-zinc-700 bg-zinc-900 shadow-xl py-1">
              {EXTRAS.filter(([id]) => !visible.some(([v]) => v === id)).map(([id, label]) => (
                <button
                  type="button"
                  key={id}
                  onClick={() => {
                    setAdded((a) => [...a, id]);
                    setAddOpen(false);
                  }}
                  className="w-full px-3 py-2 text-xs text-left text-zinc-300 hover:bg-zinc-800"
                >
                  {label}
                </button>
              ))}
              {EXTRAS.every(([id]) => visible.some(([v]) => v === id)) && (
                <div className="px-3 py-2 text-xs text-zinc-600">All filters added</div>
              )}
            </div>
          </>
        )}
      </span>
    </div>
  );
}
