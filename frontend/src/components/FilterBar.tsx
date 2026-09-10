import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, fmt } from "../api/client";
import type { ExploreParams, Filters } from "../api/types";
import { nextListName, useWatchlists } from "../lib/lists";

/* What a fresh screen starts with: decent yield, minimal liquidity,
   investment-grade, six months to run, traded within the week. */
export const DEFAULT_FILTERS: ExploreParams = {
  min_ytm: 5,
  min_turnover: 10_000,
  min_rating: "BBB-",
  min_years: 0.5,
  traded_within: 5,
};

/** v1's describeFilters(): human-readable summary of active filters. */
export function describeFilters(p: ExploreParams): string[] {
  const parts: string[] = [];
  if (p.min_rating) parts.push(`rating ≥ ${p.min_rating}`);
  if (p.min_ytm != null) parts.push(`LTP YTM ≥ ${p.min_ytm}%`);
  if (p.max_ytm != null) parts.push(`LTP YTM ≤ ${p.max_ytm}%`);
  if (p.min_years != null) parts.push(`maturity ≥ ${p.min_years}y`);
  if (p.max_years != null) parts.push(`maturity ≤ ${p.max_years}y`);
  if (p.max_ltp != null) parts.push(`price ≤ ₹${p.max_ltp.toLocaleString("en-IN")}`);
  if (p.min_turnover != null) parts.push(`retail traded ≥ ${fmt.inr(p.min_turnover)}`);
  if (p.min_otc_turnover != null) parts.push(`OTC traded ≥ ${fmt.inr(p.min_otc_turnover)}`);
  if (p.frequency) parts.push(`pays ${p.frequency}`);
  if (p.payout)
    parts.push(
      p.payout === "maturity"
        ? "pays at maturity"
        : p.payout === "bullet"
          ? "bullet repayment"
          : p.payout === "staggered"
            ? "staggered redemption"
            : p.payout,
    );
  if (p.sector) parts.push(`sector: ${p.sector}`);
  if (p.exchange) parts.push(`on ${p.exchange}`);
  if (p.retail_only) parts.push("trades retail");
  if (p.otc_only) parts.push("trades OTC");
  if (p.fresh_only) parts.push("fresh prices");
  if (p.traded_within != null)
    parts.push(`traded within ${p.traded_within} session${p.traded_within === 1 ? "" : "s"}`);
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
}

export default function FilterBar({
  params,
  onChange,
  modes,
  onModes,
  dimReason,
}: {
  params: ExploreParams;
  onChange: (p: ExploreParams) => void;
  modes: ListModes;
  onModes: (m: ListModes) => void;
  dimReason?: string;
}) {
  const { data: filters } = useQuery({ queryKey: ["filters"], queryFn: api.filters });

  const set = (patch: Partial<ExploreParams>) => {
    onChange({ ...params, ...patch });
  };

  return (
    <FilterFields
      params={params}
      set={set}
      filters={filters}
      onReset={() => onChange({ ...DEFAULT_FILTERS, q: params.q })}
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

/* Curated dropdown steps for the numeric filters. Every option is
   itself an inequality (min ⇒ ≥, max ⇒ ≤), so the endpoint options
   naturally absorb odd values — "≥ 20%" covers a 27% oddball. */
const inr = (v: number) =>
  v >= 10_000_000
    ? `₹${v / 10_000_000} Cr`
    : v >= 100_000
      ? `₹${v / 100_000} L`
      : `₹${v.toLocaleString("en-IN")}`;
const yrs = (v: number) => (v < 1 ? `${v * 12}mo` : `${v}y`);
const opts = (values: number[], label: (v: number) => string, sign: "≥" | "≤") =>
  values.map((v) => ({ v, label: `${sign} ${label(v)}` }));

const NUM_OPTIONS: Record<NumKey, Array<{ v: number; label: string }>> = {
  min_ytm: opts([4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 18, 20], (v) => `${v}%`, "≥"),
  max_ytm: opts([8, 10, 12, 14, 16, 18, 20, 25], (v) => `${v}%`, "≤"),
  min_years: opts([0.5, 1, 2, 3, 5, 7, 10], yrs, "≥"),
  max_years: opts([0.5, 1, 2, 3, 5, 7, 10, 15], yrs, "≤"),
  max_ltp: opts([1_000, 10_000, 50_000, 100_000, 200_000, 500_000], inr, "≤"),
  min_turnover: opts(
    [1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000, 5_000_000, 10_000_000],
    inr,
    "≥",
  ),
  min_otc_turnover: opts(
    [100_000, 1_000_000, 5_000_000, 10_000_000, 50_000_000, 100_000_000],
    inr,
    "≥",
  ),
  // Values are trading sessions; the labels speak calendar.
  traded_within: [
    { v: 1, label: "Today" },
    { v: 2, label: "≤ 2 days" },
    { v: 5, label: "≤ 1 week" },
    { v: 10, label: "≤ 2 weeks" },
    { v: 21, label: "≤ 1 month" },
    { v: 63, label: "≤ 3 months" },
    { v: 126, label: "≤ 6 months" },
    { v: 250, label: "≤ 1 year" },
  ],
};

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
  params: ExploreParams;
  set: (patch: Partial<ExploreParams>) => void;
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

  // Label-row ×: with onRemove (default filters) it always shows and also
  // hides the box; otherwise (extras) it appears when active and clears.
  const clearX = (key: NumKey | SelKey, onRemove?: () => void) => (
    <button
      type="button"
      title={onRemove ? "Remove filter" : "Clear"}
      onClick={() => {
        set({ [key]: undefined } as Partial<ExploreParams>);
        onRemove?.();
      }}
      className="text-zinc-600 hover:text-zinc-300 leading-none px-0.5"
    >
      ×
    </button>
  );

  const num = (label: string, key: NumKey, onRemove?: () => void) => {
    const current = params[key];
    const active = current != null;
    const options = NUM_OPTIONS[key];
    // A persisted value from before the preset lists (or a preset that
    // later changed) still displays and filters until the user moves on.
    const offList = current != null && !options.some((o) => o.v === current);
    const sign = key.startsWith("max") || key === "traded_within" ? "≤" : "≥";
    return (
      <label className="flex-1 min-w-0">
        <span className={`flex items-center justify-between ${lbl(active)}`}>
          {label}
          {(active || onRemove) && clearX(key, onRemove)}
        </span>
        <select
          className={box(active)}
          value={current ?? ""}
          onChange={(e) =>
            set({
              [key]: e.target.value === "" ? undefined : +e.target.value,
            } as Partial<ExploreParams>)
          }
        >
          <option value="">Any</option>
          {offList && <option value={current}>{`${sign} ${current}`}</option>}
          {options.map((o) => (
            <option key={o.v} value={o.v}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
    );
  };

  const sel = (label: string, key: SelKey, options: string[], onRemove?: () => void) => {
    const active = !!params[key];
    return (
      <label className="flex-1 min-w-0">
        <span className={`flex items-center justify-between ${lbl(active)}`}>
          {label}
          {(active || onRemove) && clearX(key, onRemove)}
        </span>
        <select
          className={box(active)}
          value={params[key] ?? ""}
          onChange={(e) => set({ [key]: e.target.value || undefined } as Partial<ExploreParams>)}
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
    // Range-completers first (max ends of the default mins), then
    // liquidity, issuer attributes, coupon mechanics, technical.
    ["max_ytm", "Max LTP YTM %"],
    ["max_years", "Max maturity"],
    ["max_ltp", "Ticket size"],
    ["min_otc_turnover", "OTC turnover"],
    ["segment", "Segment"],
    ["sector", "Sector"],
    ["security", "Security"],
    ["frequency", "Pays"],
    ["payout", "Payout"],
    ["exchange", "Exchange"],
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

  // The five essentials are removable too: × hides the box (and clears
  // its value); the + Filter menu brings it back. Layout preference, so
  // it persists — and survives Clear, which resets values, not layout.
  const DEFAULT_FIELDS = [
    ["min_ytm", "Min LTP YTM %"],
    ["min_rating", "Min rating"],
    ["min_years", "Min mat (y)"],
    ["min_turnover", "Retail trd ₹"],
    ["traded_within", "Last traded"],
  ] as const;
  type DefaultId = (typeof DEFAULT_FIELDS)[number][0];
  const [hiddenDefaults, setHiddenDefaults] = useState<DefaultId[]>(() => {
    try {
      return JSON.parse(localStorage.getItem("explore-hidden-filters") ?? "[]");
    } catch {
      return [];
    }
  });
  useEffect(() => {
    localStorage.setItem("explore-hidden-filters", JSON.stringify(hiddenDefaults));
  }, [hiddenDefaults]);
  // A set value always wins visibility (e.g. Clear restoring min_rating).
  const defaultShown = (id: DefaultId) => !hiddenDefaults.includes(id) || params[id] != null;
  const hideDefault = (id: DefaultId) =>
    setHiddenDefaults((h) => (h.includes(id) ? h : [...h, id]));
  const hiddenDefaultFields = DEFAULT_FIELDS.filter(([id]) => !defaultShown(id));

  const clearExtra: Record<ExtraId, Partial<ExploreParams>> = {
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

  // Extras carry their remove × in the label row, same as the defaults.
  const removeX = (onRemove: () => void) => (
    <button
      type="button"
      title="Remove filter"
      onClick={onRemove}
      className="text-zinc-600 hover:text-zinc-300 leading-none px-0.5"
    >
      ×
    </button>
  );

  const extraControl = (id: ExtraId, onRemove: () => void) => {
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
          <label className="flex-1 min-w-0">
            <span className={`flex items-center justify-between ${lbl(!!v)}`}>
              Segment
              {removeX(onRemove)}
            </span>
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
        return num("Max LTP YTM %", "max_ytm", onRemove);
      case "sector":
        return sel("Sector", "sector", filters?.sectors ?? [], onRemove);
      case "frequency":
        return sel("Pays", "frequency", filters?.frequencies ?? [], onRemove);
      case "payout":
        return (
          <label className="flex-1 min-w-0">
            <span className={`flex items-center justify-between ${lbl(!!params.payout)}`}>
              Payout
              {removeX(onRemove)}
            </span>
            <select
              className={box(!!params.payout)}
              value={params.payout ?? ""}
              onChange={(e) => set({ payout: e.target.value || undefined })}
            >
              <option value="">Any</option>
              <option value="bullet">Bullet</option>
              <option value="staggered">Staggered</option>
              <option value="maturity">Pays at maturity</option>
              <option value="perpetual">Perpetual</option>
            </select>
          </label>
        );
      case "exchange":
        return sel("Exchange", "exchange", ["NSE", "BSE"], onRemove);
      case "max_years":
        return num("Max mat (y)", "max_years", onRemove);
      case "max_ltp":
        return num("Ticket ₹ max", "max_ltp", onRemove);
      case "min_otc_turnover":
        return num("OTC trd ₹", "min_otc_turnover", onRemove);
      case "security": {
        const v = params.secured_only ? "secured" : params.unsecured_only ? "unsecured" : "";
        return (
          <label className="flex-1 min-w-0">
            <span className={`flex items-center justify-between ${lbl(!!v)}`}>
              Security
              {removeX(onRemove)}
            </span>
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
          <label className="flex-1 min-w-0">
            <span className={`flex items-center justify-between ${lbl(!!params.exact_only)}`}>
              YTM basis
              {removeX(onRemove)}
            </span>
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
        {DEFAULT_FIELDS.filter(([id]) => defaultShown(id)).map(([id, label]) =>
          id === "min_rating" ? (
            <span key={id} className="flex items-end gap-1">
              {sel(label, id, filters?.ratings ?? [], () => hideDefault(id))}
            </span>
          ) : (
            <span key={id} className="flex items-end gap-1">
              {num(label, id, () => hideDefault(id))}
            </span>
          ),
        )}

        {visible.map(([id]) => (
          <span key={id} className="flex items-end gap-1">
            {extraControl(id, () => {
              setAdded((a) => a.filter((x) => x !== id));
              set(clearExtra[id]);
            })}
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
                {hiddenDefaultFields.map(([id, label]) => (
                  <button
                    type="button"
                    key={id}
                    onClick={() => {
                      setHiddenDefaults((h) => h.filter((x) => x !== id));
                      setAddOpen(false);
                    }}
                    className="w-full px-3 py-2 text-xs text-left text-zinc-300 hover:bg-zinc-800"
                  >
                    {label}
                  </button>
                ))}
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
                {hiddenDefaultFields.length === 0 &&
                  EXTRAS.every(([id]) => visible.some(([v]) => v === id)) && (
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
          title="Reset filters and layout to the defaults"
          onClick={() => {
            setHiddenDefaults([]);
            setAdded([]);
            onReset();
          }}
          className="px-2.5 py-1.5 rounded-md border border-zinc-700 text-xs font-medium text-red-400 hover:border-red-400 transition-colors whitespace-nowrap"
        >
          Reset
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
    : "border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:text-zinc-300";

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
    <span
      className={`relative flex items-stretch rounded-md border text-xs font-medium transition-colors ${active}`}
    >
      <button
        type="button"
        title={selected ? `Only bonds in ${selected.name}` : "Only your starred bonds"}
        onClick={() => onModes({ ...modes, watchlist: !modes.watchlist })}
        className="pl-2.5 pr-2 py-1.5 rounded-l-md whitespace-nowrap max-w-40 truncate hover:bg-zinc-800/60"
      >
        ★ {label}
      </button>
      <button
        type="button"
        title="Choose or manage watchlists"
        onClick={() => (open ? close() : setOpen(true))}
        className="px-1.5 rounded-r-md border-l border-zinc-700/70 hover:bg-zinc-800/60 flex items-center"
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path
            d="M4 6l4 4 4-4"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
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
