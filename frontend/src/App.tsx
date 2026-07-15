import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "./api/client";
import type { ScreenerParams } from "./api/types";
import BondDrawer, { type Tab } from "./components/BondDrawer";
import FilterBar, { DEFAULT_FILTERS, type ListModes } from "./components/FilterBar";
import ScreenerTable from "./components/ScreenerTable";
import { HOLDINGS_KEY, useStoredList, WATCHLIST_KEY } from "./lib/lists";

export default function App() {
  const [params, setParams] = useState<ScreenerParams>(DEFAULT_FILTERS);
  const [modes, setModes] = useState<ListModes>({ watchlist: false, holdings: false });
  const [selected, setSelected] = useState<{ isin: string; tab?: Tab } | null>(null);
  const watchlist = useStoredList(WATCHLIST_KEY);
  const holdings = useStoredList(HOLDINGS_KEY);

  // A search means "find me this bond" — the screener filters would only
  // hide what the user is explicitly looking for, so q bypasses them.
  // Watchlist/holdings modes work the same way: the browser-stored lists
  // are fetched by explicit ISINs, never narrowed by the other filters.
  const listMode = modes.watchlist || modes.holdings;
  const listIsins = listMode
    ? [
        ...new Set([
          ...(modes.watchlist ? watchlist.items : []),
          ...(modes.holdings ? holdings.items : []),
        ]),
      ]
    : [];
  const effectiveParams: ScreenerParams = params.q
    ? { q: params.q }
    : listMode
      ? { isins: listIsins.join(",") }
      : params;
  const bonds = useQuery({
    queryKey: ["bonds", effectiveParams],
    queryFn: () => api.bonds({ ...effectiveParams, limit: 1000 }),
    enabled: !listMode || listIsins.length > 0,
  });

  return (
    <div className="min-h-screen">
      <div className="max-w-screen-2xl mx-auto px-6 py-8">
        <header className="flex items-center gap-4 mb-8">
          <h1 className="text-3xl font-extrabold tracking-tight">
            Bond<span className="text-emerald-400">Lens</span>
          </h1>
          <div className="ml-auto flex items-center gap-3">
            <div className="relative">
              <input
                type="text"
                placeholder="Search company, symbol, ISIN, rating"
                className="w-72 bg-transparent border border-zinc-700 rounded-md pl-2.5 pr-7 py-1.5 text-xs font-medium focus:outline-none focus:border-zinc-400 placeholder:text-zinc-600"
                value={params.q ?? ""}
                onChange={(e) => setParams({ ...params, q: e.target.value || undefined })}
              />
              {params.q && (
                <button
                  type="button"
                  title="Clear search"
                  onClick={() => setParams({ ...params, q: undefined })}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-200 text-sm leading-none"
                >
                  ×
                </button>
              )}
            </div>
          </div>
        </header>

        <main className="space-y-6">
          <FilterBar
            params={params}
            onChange={setParams}
            modes={modes}
            onModes={setModes}
            dimReason={
              params.q
                ? "Filters are ignored while searching"
                : modes.watchlist
                  ? "Filters are ignored while viewing the watchlist"
                  : modes.holdings
                    ? "Filters are ignored while viewing holdings"
                    : undefined
            }
          />
          <ScreenerTable
            bonds={listMode && listIsins.length === 0 ? [] : (bonds.data?.bonds ?? [])}
            loading={bonds.isLoading && (!listMode || listIsins.length > 0)}
            error={bonds.error?.message ?? null}
            onSelect={(isin, tab) => setSelected({ isin, tab })}
          />
        </main>
      </div>

      {selected && (
        <BondDrawer
          key={selected.isin}
          isin={selected.isin}
          initialTab={selected.tab}
          onSelect={(isin) => setSelected({ isin })}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
