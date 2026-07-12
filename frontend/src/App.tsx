import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "./api/client";
import type { ScreenerParams } from "./api/types";
import BondDrawer from "./components/BondDrawer";
import FilterBar from "./components/FilterBar";
import ScreenerTable from "./components/ScreenerTable";
import StatusBar from "./components/StatusBar";

export default function App() {
  const [params, setParams] = useState<ScreenerParams>({});
  const [selected, setSelected] = useState<string | null>(null);

  const bonds = useQuery({
    queryKey: ["bonds", params],
    queryFn: () => api.bonds({ ...params, limit: 1000 }),
  });

  return (
    <div className="min-h-screen">
      <header className="border-b border-zinc-800 bg-zinc-950/90 backdrop-blur sticky top-0 z-30">
        <div className="max-w-screen-2xl mx-auto px-4 py-3 flex items-center justify-between gap-4">
          <div className="flex items-baseline gap-3">
            <h1 className="text-xl font-bold tracking-tight">
              Bond<span className="text-emerald-400">Lens</span>
            </h1>
            <span className="text-xs text-zinc-500">
              {bonds.data ? `${bonds.data.count.toLocaleString()} bonds` : "…"}
            </span>
          </div>
          <StatusBar />
        </div>
      </header>

      <main className="max-w-screen-2xl mx-auto px-4 py-4 space-y-4">
        <FilterBar params={params} onChange={setParams} />
        <ScreenerTable
          bonds={bonds.data?.bonds ?? []}
          loading={bonds.isLoading}
          error={bonds.error?.message ?? null}
          onSelect={setSelected}
        />
      </main>

      {selected && <BondDrawer isin={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
