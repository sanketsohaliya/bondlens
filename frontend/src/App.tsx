import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "./api/client";
import type { ExploreParams } from "./api/types";
import { GOTO_PORTFOLIO_EVENT } from "./components/AddLotPopover";
import BondDrawer, { type Tab } from "./components/BondDrawer";
import ExploreTable, { type SortState } from "./components/ExploreTable";
import FilterBar, { DEFAULT_FILTERS, type ListModes } from "./components/FilterBar";
import Logo from "./components/Logo";
import NotificationsBell from "./components/NotificationsBell";
import { useAuth } from "./lib/auth";
import { unionIsins, useWatchlists } from "./lib/lists";
import Portfolio from "./pages/Portfolio";

type Page = "explore" | "portfolio";

export default function App() {
  const [page, setPage] = useState<Page>("explore");
  const [params, setParams] = useState<ExploreParams>(DEFAULT_FILTERS);
  const [modes, setModes] = useState<ListModes>({
    watchlist: false,
    watchlistId: null,
  });
  const [selected, setSelected] = useState<{ isin: string; tab?: Tab } | null>(null);
  const [sort, setSort] = useState<SortState>({ key: "turnover", dir: -1 });
  const { lists: watchlists } = useWatchlists();

  // Deep components (add-lot popover) can ask to open the portfolio page.
  useEffect(() => {
    const go = () => setPage("portfolio");
    window.addEventListener(GOTO_PORTFOLIO_EVENT, go);
    return () => window.removeEventListener(GOTO_PORTFOLIO_EVENT, go);
  }, []);

  // A search means "find me this bond" — the explore filters would only
  // hide what the user is explicitly looking for, so q bypasses them.
  // Watchlist mode works the same way: the stored lists are fetched by
  // explicit ISINs, never narrowed by the other filters.
  const listMode = modes.watchlist;
  const listIsins = listMode ? unionIsins(watchlists, modes.watchlistId) : [];
  const effectiveParams: ExploreParams = params.q
    ? { q: params.q }
    : listMode
      ? { isins: listIsins.join(",") }
      : params;
  // The sort travels to the API so the 1000-row page is cut in the same
  // order the table displays — otherwise sorting only rearranges whatever
  // the default turnover ordering happened to include.
  const sortParams = { sort: sort.key, dir: sort.dir === 1 ? ("asc" as const) : ("desc" as const) };
  const bonds = useQuery({
    queryKey: ["bonds", effectiveParams, sortParams],
    queryFn: () => api.bonds({ ...effectiveParams, ...sortParams, limit: 1000 }),
    enabled: !listMode || listIsins.length > 0,
  });

  return (
    <div className="min-h-screen">
      <div className="max-w-screen-2xl mx-auto px-6 py-8">
        <header className="flex items-center gap-4 mb-8">
          <button
            type="button"
            title="Home"
            onClick={() => {
              setPage("explore");
              setParams({ ...params, q: undefined });
            }}
            className="flex items-center gap-2.5 hover:opacity-90 transition-opacity"
          >
            <Logo size={36} className="rounded-lg ring-1 ring-zinc-800" />
            <h1 className="text-3xl font-extrabold tracking-tight">
              Bond<span className="text-emerald-400">Lens</span>
            </h1>
          </button>
          <nav className="flex items-center gap-1 ml-4">
            {(
              [
                ["explore", "Explore"],
                ["portfolio", "Portfolio"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setPage(id)}
                className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                  page === id ? "bg-zinc-800 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {label}
              </button>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <div className="relative">
              <input
                type="text"
                placeholder="Search company, symbol, ISIN, rating"
                className="w-72 bg-transparent border border-zinc-700 rounded-md pl-2.5 pr-7 py-1.5 text-xs font-medium focus:outline-none focus:border-zinc-400 placeholder:text-zinc-600"
                value={params.q ?? ""}
                onChange={(e) => {
                  setParams({ ...params, q: e.target.value || undefined });
                  if (e.target.value) setPage("explore");
                }}
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
            <NotificationsBell onSelect={(isin) => setSelected({ isin })} />
            <UserMenu />
          </div>
        </header>

        <main className="space-y-6">
          {page === "portfolio" ? (
            <Portfolio onSelect={(isin, tab) => setSelected({ isin, tab })} />
          ) : (
            <>
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
                      : undefined
                }
              />
              <ExploreTable
                bonds={listMode && listIsins.length === 0 ? [] : (bonds.data?.bonds ?? [])}
                loading={bonds.isLoading && (!listMode || listIsins.length > 0)}
                error={bonds.error?.message ?? null}
                onSelect={(isin, tab) => setSelected({ isin, tab })}
                onSortChange={setSort}
              />
            </>
          )}
        </main>

        <footer className="mt-10 pt-4 border-t border-zinc-800 text-xs leading-relaxed text-zinc-500">
          Not investment advice. Prices, yields and ratings are derived from exchange/depository
          data that may be delayed, incomplete or inaccurate; verify independently before acting.
          Bonds carry credit and liquidity risk, including loss of principal.
        </footer>
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

function UserMenu() {
  const { user, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        title={user.email}
        className="block rounded-full border border-zinc-700 hover:border-zinc-400 transition-colors"
      >
        {user.picture ? (
          <img
            src={user.picture}
            alt=""
            referrerPolicy="no-referrer"
            className="w-8 h-8 rounded-full"
          />
        ) : (
          <span className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-zinc-300">
            {(user.name ?? user.email)[0]?.toUpperCase()}
          </span>
        )}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} aria-hidden="true" />
          <div className="absolute right-0 top-full mt-2 z-30 w-56 rounded-lg border border-zinc-700 bg-zinc-900 shadow-xl py-1">
            <div className="px-3 py-2 border-b border-zinc-800">
              <div className="text-xs font-medium text-zinc-200 truncate">
                {user.name ?? "\u2014"}
              </div>
              <div className="text-[11px] text-zinc-500 truncate">{user.email}</div>
            </div>
            <button
              type="button"
              onClick={signOut}
              className="w-full px-3 py-2 text-xs text-left text-zinc-300 hover:bg-zinc-800"
            >
              Sign out
            </button>
          </div>
        </>
      )}
    </div>
  );
}
