import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "./api/client";
import type { ExploreParams } from "./api/types";
import { GOTO_PORTFOLIO_EVENT } from "./components/AddLotPopover";
import BondDrawer, { type Tab } from "./components/BondDrawer";
import Disclaimer from "./components/Disclaimer";
import ExploreTable, { type SortState } from "./components/ExploreTable";
import FilterBar, { DEFAULT_FILTERS, type ListModes } from "./components/FilterBar";
import Logo from "./components/Logo";
import NotificationsBell from "./components/NotificationsBell";
import { alertClickTarget, parseDeepLink } from "./lib/alertRules";
import { useAuth } from "./lib/auth";
import { unionIsins, useWatchlists } from "./lib/lists";
import Orders from "./pages/Orders";
import Portfolio from "./pages/Portfolio";

type Page = "explore" | "portfolio" | "orders";

// Filters and sort survive reloads — set them once, they stick. Search
// (q) and list views (isins) are session things, never persisted.
const SAVED_FILTERS_KEY = "explore-filters";
const SAVED_SORT_KEY = "explore-sort";
const DEFAULT_SORT: SortState = { key: "ask_ytm", dir: -1 };

/** Saved state wins outright; `fallback` only seeds a first visit. A
 * filter cleared to "Any" is `undefined`, which JSON drops - merging the
 * saved object over the defaults would quietly refill it (Last traded
 * <= 5 sessions came back after every reload and hid never-traded paper). */
function loadSaved<T extends object>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export default function App() {
  const [page, setPage] = useState<Page>("explore");
  // A deep link (/?bond=ISIN[&tab=]) is read once at load and the query
  // dropped so a reload is a plain load. Alert pushes ask for the bond's
  // row (search = ISIN); other tabs open the drawer.
  const [deepLink] = useState(() => {
    const link = parseDeepLink(window.location.search);
    if (link) window.history.replaceState(null, "", window.location.pathname);
    return link;
  });
  const [params, setParams] = useState<ExploreParams>(() => {
    const saved = loadSaved<ExploreParams>(SAVED_FILTERS_KEY, DEFAULT_FILTERS);
    return deepLink && alertClickTarget(deepLink.tab) === "row"
      ? { ...saved, q: deepLink.isin }
      : saved;
  });
  const [modes, setModes] = useState<ListModes>({
    watchlist: false,
    watchlistId: null,
  });
  const [selected, setSelected] = useState<{ isin: string; tab?: Tab } | null>(() =>
    deepLink && alertClickTarget(deepLink.tab) === "drawer"
      ? { isin: deepLink.isin, tab: deepLink.tab as Tab | undefined }
      : null,
  );
  const [sort, setSort] = useState<SortState>(() => loadSaved(SAVED_SORT_KEY, DEFAULT_SORT));

  // Isolate one bond on Explore: the search path shows just that row with
  // its live ask. Used by alert hits, exit targets, opportunity
  // notifications and the push deep link.
  const showBond = (isin: string) => {
    setPage("explore");
    setParams((p) => ({ ...p, q: isin }));
    setSelected(null);
  };
  const openFromAlert = (isin: string, tab?: Tab) =>
    alertClickTarget(tab) === "row" ? showBond(isin) : setSelected({ isin, tab });
  const { lists: watchlists } = useWatchlists();

  useEffect(() => {
    const { q: _q, isins: _isins, ...filters } = params;
    localStorage.setItem(SAVED_FILTERS_KEY, JSON.stringify(filters));
  }, [params]);
  useEffect(() => {
    localStorage.setItem(SAVED_SORT_KEY, JSON.stringify(sort));
  }, [sort]);

  // Deep components (add-lot popover) can ask to open the portfolio page.
  useEffect(() => {
    const go = () => setPage("portfolio");
    window.addEventListener(GOTO_PORTFOLIO_EVENT, go);
    return () => window.removeEventListener(GOTO_PORTFOLIO_EVENT, go);
  }, []);

  // Orders tab exists whenever Kite is connected — even with no orders
  // it carries the funds snapshot. The page fetches and polls its own
  // data once opened.
  const { data: kite } = useQuery({
    queryKey: ["kite-status"],
    queryFn: api.kite.status,
    staleTime: 120_000,
    refetchOnWindowFocus: false,
  });
  const kiteConnected = kite?.connected ?? false;
  // Keep the tab while the user is standing on it, so a connection blip
  // doesn't yank the page away mid-interaction.
  const showOrdersTab = kiteConnected || page === "orders";
  useEffect(() => {
    if (page === "orders" && !kiteConnected) setPage("explore");
  }, [page, kiteConnected]);

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
  // The FULL filtered universe travels to the client (a server page cut
  // ordered before the user's sort hides whatever falls past the edge).
  // Sorting happens entirely client-side, so sort changes never refetch;
  // the table truncates the RENDER after ordering, not the data.
  const bonds = useQuery({
    queryKey: ["bonds", effectiveParams],
    queryFn: () => api.bonds({ ...effectiveParams, limit: 100_000 }),
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
                ...(showOrdersTab ? ([["orders", "Orders"]] as const) : []),
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
            <NotificationsBell onSelect={openFromAlert} />
            <UserMenu />
          </div>
        </header>

        <main className="space-y-6">
          {page === "orders" ? (
            <Orders onSelect={(isin) => setSelected({ isin })} />
          ) : page === "portfolio" ? (
            <Portfolio onSelect={(isin, tab) => setSelected({ isin, tab })} />
          ) : (
            <>
              <FilterBar
                params={params}
                onChange={setParams}
                modes={modes}
                onModes={setModes}
                onSelect={(isin) => showBond(isin)}
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
                initialSort={sort}
                onSortChange={setSort}
              />
            </>
          )}
        </main>

        <footer className="mt-10 pt-4 border-t border-zinc-800">
          <Disclaimer />
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
