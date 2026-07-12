import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "./api/client";
import type { ScreenerParams } from "./api/types";
import BondDrawer, { type Tab } from "./components/BondDrawer";
import FilterBar, { DEFAULT_PRESET } from "./components/FilterBar";
import ScreenerTable from "./components/ScreenerTable";

export default function App() {
  const [params, setParams] = useState<ScreenerParams>(DEFAULT_PRESET.params);
  const [selected, setSelected] = useState<{ isin: string; tab?: Tab } | null>(null);

  const bonds = useQuery({
    queryKey: ["bonds", params],
    queryFn: () => api.bonds({ ...params, limit: 1000 }),
  });

  return (
    <div className="min-h-screen">
      <div className="max-w-screen-2xl mx-auto px-6 py-8">
        <header className="flex items-center gap-4 mb-8">
          <h1 className="text-3xl font-extrabold tracking-tight">
            Bond<span className="text-emerald-400">Lens</span>
          </h1>
          <div className="ml-auto flex items-center gap-3">
            <input
              type="text"
              placeholder="Search company, symbol, ISIN, rating"
              className="w-72 bg-transparent border border-zinc-700 rounded-md px-2.5 py-1.5 text-xs font-medium focus:outline-none focus:border-emerald-500 placeholder:text-zinc-600"
              value={params.q ?? ""}
              onChange={(e) => setParams({ ...params, q: e.target.value || undefined })}
            />
          </div>
        </header>

        <main className="space-y-6">
          <FilterBar params={params} onChange={setParams} />
          <ScreenerTable
            bonds={bonds.data?.bonds ?? []}
            loading={bonds.isLoading}
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
