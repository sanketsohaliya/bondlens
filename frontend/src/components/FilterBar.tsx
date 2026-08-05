import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, fmt } from "../api/client";
import type { Filters, ScreenerParams } from "../api/types";
import { nextListName, useWatchlists } from "../lib/lists";

/* What a fresh screen starts with: decent yield, real retail liquidity,
   investment-grade, at least a year to run, recently traded. */
export const DEFAULT_FILTERS: ScreenerParams = {
  min_ytm: 8,
  min_turnover: 51000,
  min_rating: "BBB-",
  min_years: 1,
  traded_within: 1,
};

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
  if (p.payout) parts.push(p.payout === "maturity" ? "pays at maturity" : "periodic payout");
  if (p.sector) parts.push(`sector: ${p.sector}`);
  if (p.exchange) parts.push(`on ${p.exchange}`);
  if (p.retail_only) parts.push("trades retail");
  if (p.otc_only) parts.push("trades OTC");
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

export interface ListModes {
  watchlist: boolean;
  /** null = all watchlists combined; otherwise a single list id. */
  watchlistId: number | null;
  holdings: boolean;
}

export default function FilterBar({
  params,
  onChange,
  modes,
  onModes,
  dimReason,
}: {
  params: ScreenerParams;
  onChange: (p: ScreenerParams) => void;
  modes: ListModes;
  onModes: (m: ListModes) => void;
  dimReason?: string;
}) {
  const { data: filters } = useQuery({ queryKey: ["filters"], queryFn: api.filters });

  const set = (patch: Partial<ScreenerParams>) => {
    onChange({ ...params, ...patch });
  };

  return (
    <FilterFields
      params={params}
      set={set}
      filters={filters}
      onReset={() => onChange({ q: params.q })}
      modes={modes}
      onModes={onModes}
      dimReason={dimReason}
    />
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
  onReset,
  modes,
  onModes,
  dimReason,
}: {
  params: ScreenerParams;
  set: (patch: Partial<ScreenerParams>) => void;
  filters: Filters | undefined;
  onReset: () => void;
  modes: ListModes;
  onModes: (m: ListModes) => void;
  dimReason?: string;
}) {
  const lbl = (active: boolean) =>
    `block text-[10px] font-semibold uppercase tracking-wider mb-1.5 whitespace-nowrap ${
      active ? "text-zinc-300" : "text-zinc-500"
    }`;
  const box = (active: boolean) =>
    "w-full bg-transparent border rounded-md px-2 py-1.5 text-xs font-medium " +
    "focus:outline-none focus:border-zinc-400 placeholder:text-zinc-600 " +
    (active ? "border-zinc-500 text-zinc-100" : "border-zinc-700 text-zinc-600");

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
    ["payout", "Payout"],
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
    payout: !!params.payout,
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
    payout: { payout: undefined },
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
      case "payout":
        return (
          <label className="flex-1">
            <span className={lbl(!!params.payout)}>Payout</span>
            <select
              className={box(!!params.payout)}
              value={params.payout ?? ""}
              onChange={(e) => set({ payout: e.target.value || undefined })}
            >
              <option value="">Any</option>
              <option value="maturity">At maturity (zero coupon / cumulative)</option>
              <option value="periodic">Periodic coupons</option>
            </select>
          </label>
        );
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
    <div className="flex items-end gap-3">
      <div
        className={`flex-1 grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] items-end gap-3 transition-opacity ${
          dimReason ? "opacity-40" : ""
        }`}
        title={dimReason}
      >
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
              <div className="absolute left-0 top-full mt-2 z-30 w-44 rounded-lg border border-zinc-700 bg-zinc-900 shadow-xl py-1">
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

      <span className="flex items-end gap-2 self-end">
        <WatchlistChip modes={modes} onModes={onModes} />
        <button
          type="button"
          title="Only bonds you hold"
          onClick={() => onModes({ ...modes, holdings: !modes.holdings })}
          className={`px-2.5 py-1.5 rounded-md border text-xs font-medium transition-colors whitespace-nowrap ${
            modes.holdings
              ? "border-zinc-400 text-zinc-100 bg-zinc-800/60"
              : "border-zinc-700 text-zinc-400 hover:border-zinc-500"
          }`}
        >
          ◆ Holdings
        </button>
        <button
          type="button"
          title="Remove all filters and show everything"
          onClick={onReset}
          className="px-2.5 py-1.5 rounded-md border border-zinc-700 text-xs font-medium text-red-400 hover:border-red-400 transition-colors whitespace-nowrap"
        >
          Clear
        </button>
      </span>
    </div>
  );
}

/* Split chip: the label toggles watchlist view (all lists, or the picked
   one); the caret opens a dropdown to pick / rename / delete / create
   lists. Same footprint as the old single-purpose chip. */
function WatchlistChip({ modes, onModes }: { modes: ListModes; onModes: (m: ListModes) => void }) {
  const { lists, create, rename, remove } = useWatchlists();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<{ id: number; name: string } | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const [draft, setDraft] = useState<string | null>(null);

  const selected = modes.watchlistId != null ? lists.find((l) => l.id === modes.watchlistId) : null;
  const label = selected ? selected.name : "Watchlist";
  const active = modes.watchlist
    ? "border-zinc-400 text-zinc-100 bg-zinc-800/60"
    : "border-zinc-700 text-zinc-400 hover:border-zinc-500";

  const close = () => {
    setOpen(false);
    setEditing(null);
    setConfirmId(null);
    setDraft(null);
  };

  const pick = (id: number | null) => {
    onModes({ ...modes, watchlist: true, watchlistId: id });
    close();
  };

  const submitDraft = () => {
    const name = draft?.trim();
    if (name) create(name);
    setDraft(null);
  };

  const submitEdit = () => {
    if (editing) {
      const name = editing.name.trim();
      if (name) rename(editing.id, name);
    }
    setEditing(null);
  };

  return (
    <span className="relative flex">
      <button
        type="button"
        title={selected ? `Only bonds in ${selected.name}` : "Only your starred bonds"}
        onClick={() => onModes({ ...modes, watchlist: !modes.watchlist })}
        className={`pl-2.5 pr-1.5 py-1.5 rounded-l-md border border-r-0 text-xs font-medium transition-colors whitespace-nowrap max-w-40 truncate ${active}`}
      >
        ★ {label}
      </button>
      <button
        type="button"
        title="Choose or manage watchlists"
        onClick={() => (open ? close() : setOpen(true))}
        className={`pl-1 pr-1.5 py-1.5 rounded-r-md border border-l-0 text-[10px] transition-colors ${active}`}
      >
        ▾
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={close} aria-hidden="true" />
          <div className="absolute right-0 top-full mt-2 z-30 w-56 rounded-lg border border-zinc-700 bg-zinc-900 shadow-xl py-1">
            <button
              type="button"
              onClick={() => pick(null)}
              className={`w-full px-3 py-2 text-xs text-left hover:bg-zinc-800 ${
                modes.watchlistId == null ? "text-zinc-100 font-medium" : "text-zinc-300"
              }`}
            >
              All watchlists
            </button>
            {lists.map((l) =>
              editing?.id === l.id ? (
                <span key={l.id} className="block px-3 py-1.5">
                  <input
                    // biome-ignore lint/a11y/noAutofocus: the user just asked to rename this list
                    autoFocus
                    value={editing.name}
                    onChange={(e) => setEditing({ id: l.id, name: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") submitEdit();
                      if (e.key === "Escape") setEditing(null);
                    }}
                    onBlur={submitEdit}
                    className="w-full bg-transparent border border-zinc-600 rounded px-2 py-1 text-xs text-zinc-100 focus:outline-none focus:border-zinc-400"
                  />
                </span>
              ) : (
                <span key={l.id} className="group flex items-center hover:bg-zinc-800">
                  <button
                    type="button"
                    onClick={() => pick(l.id)}
                    className={`flex-1 min-w-0 px-3 py-2 text-xs text-left truncate ${
                      modes.watchlistId === l.id ? "text-zinc-100 font-medium" : "text-zinc-300"
                    }`}
                  >
                    {l.name}
                    <span className="ml-1.5 text-zinc-600">{l.isins.length}</span>
                  </button>
                  <button
                    type="button"
                    title={`Rename ${l.name}`}
                    onClick={() => {
                      setConfirmId(null);
                      setEditing({ id: l.id, name: l.name });
                    }}
                    className="px-1 text-zinc-600 hover:text-zinc-200 text-xs opacity-0 group-hover:opacity-100"
                  >
                    ✎
                  </button>
                  <button
                    type="button"
                    title={confirmId === l.id ? "Click again to delete" : `Delete ${l.name}`}
                    onClick={() => {
                      if (confirmId === l.id) {
                        remove(l.id);
                        setConfirmId(null);
                        if (modes.watchlistId === l.id) {
                          onModes({ ...modes, watchlistId: null });
                        }
                      } else {
                        setConfirmId(l.id);
                      }
                    }}
                    className={`px-1.5 text-xs ${
                      confirmId === l.id
                        ? "text-red-400"
                        : "text-zinc-600 hover:text-red-400 opacity-0 group-hover:opacity-100"
                    }`}
                  >
                    {confirmId === l.id ? "sure?" : "✕"}
                  </button>
                </span>
              ),
            )}
            {draft == null ? (
              <button
                type="button"
                onClick={() => setDraft(nextListName(lists))}
                className="w-full px-3 py-2 text-xs text-left text-emerald-400 hover:bg-zinc-800"
              >
                + New list
              </button>
            ) : (
              <span className="block px-3 py-1.5">
                <input
                  // biome-ignore lint/a11y/noAutofocus: the user just asked to type a list name
                  autoFocus
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") submitDraft();
                    if (e.key === "Escape") setDraft(null);
                  }}
                  onBlur={submitDraft}
                  placeholder="List name"
                  className="w-full bg-transparent border border-zinc-600 rounded px-2 py-1 text-xs text-zinc-100 focus:outline-none focus:border-zinc-400"
                />
              </span>
            )}
          </div>
        </>
      )}
    </span>
  );
}
