import type {
  BondDetail,
  BondsResponse,
  ExploreParams,
  Filters,
  ListsResponse,
  LotInput,
  OrderBook,
  PortfolioLot,
  PortfolioResponse,
  Status,
  TradesResponse,
  User,
  Watchlist,
} from "./types";

/** Fired when any API call returns 401 — AuthProvider flips to the
 * sign-in screen (session expired or revoked mid-use). */
export const UNAUTHORIZED_EVENT = "bondlens:unauthorized";

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/api/auth/")) {
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }
    throw new Error(`${res.status} ${await res.text()}`);
  }
  return res.json() as Promise<T>;
}

async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "" && v !== false) {
      qs.set(k, String(v));
    }
  }
  return request<T>("GET", qs.size ? `${path}?${qs}` : path);
}

export const api = {
  bonds: (params: ExploreParams) =>
    get<BondsResponse>("/api/bonds", params as Record<string, unknown>),
  bond: (isin: string, price?: number) =>
    get<BondDetail>(`/api/bonds/${isin}`, price ? { price } : undefined),
  trades: (
    isin: string,
    params?: { from?: string; to?: string; exchange?: string; segment?: string },
  ) => get<TradesResponse>(`/api/bonds/${isin}/trades`, params),
  depth: (isin: string) => get<OrderBook>(`/api/bonds/${isin}/depth`),
  status: () => get<Status>("/api/status"),
  filters: () => get<Filters>("/api/filters"),
  auth: {
    google: (credential: string) => request<User>("POST", "/api/auth/google", { credential }),
    me: () => get<User>("/api/auth/me"),
    logout: () => request<{ ok: boolean }>("POST", "/api/auth/logout"),
  },
  lists: {
    get: () => get<ListsResponse>("/api/lists"),
  },
  portfolio: {
    get: () => get<PortfolioResponse>("/api/portfolio"),
    addLot: (isin: string, lot: LotInput) =>
      request<PortfolioLot>("POST", "/api/portfolio/lots", { isin, ...lot }),
    updateLot: (id: number, lot: LotInput) =>
      request<PortfolioLot>("PATCH", `/api/portfolio/lots/${id}`, lot),
    deleteLot: (id: number) => request<unknown>("DELETE", `/api/portfolio/lots/${id}`),
  },
  watchlists: {
    create: (name: string) => request<Watchlist>("POST", "/api/watchlists", { name }),
    rename: (id: number, name: string) =>
      request<unknown>("PATCH", `/api/watchlists/${id}`, { name }),
    remove: (id: number) => request<unknown>("DELETE", `/api/watchlists/${id}`),
    addIsin: (id: number, isin: string) => request<unknown>("PUT", `/api/watchlists/${id}/${isin}`),
    removeIsin: (id: number, isin: string) =>
      request<unknown>("DELETE", `/api/watchlists/${id}/${isin}`),
  },
};

export const fmt = {
  num: (v: number | null | undefined, digits = 2): string =>
    v == null ? "–" : v.toLocaleString("en-IN", { maximumFractionDigits: digits }),
  inr: (v: number | null | undefined): string =>
    v == null
      ? "–"
      : v >= 1e7
        ? `₹${(v / 1e7).toFixed(2)} Cr`
        : v >= 1e5
          ? `₹${(v / 1e5).toFixed(2)} L`
          : `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`,
  pct: (v: number | null | undefined): string => (v == null ? "–" : `${v.toFixed(2)}%`),
  // Full ₹ amount, never shortened to L/Cr (prices, face values).
  // Paise shown only below 10,000 where they are still meaningful.
  inrFull: (v: number | null | undefined): string =>
    v == null
      ? "–"
      : `₹${v.toLocaleString("en-IN", {
          maximumFractionDigits: Math.abs(v) >= 10_000 ? 0 : 2,
        })}`,
  // Decimal years -> "1y 9m" (months rounded; "9m" under a year, "2y" on exact years).
  yearsMonths: (v: number | null | undefined): string => {
    if (v == null) return "–";
    const months = Math.round(v * 12);
    const y = Math.floor(months / 12);
    const m = months % 12;
    if (y === 0) return `${m}m`;
    return m === 0 ? `${y}y` : `${y}y ${m}m`;
  },
  date: (v: string | null | undefined): string =>
    v == null
      ? "–"
      : new Date(v).toLocaleDateString("en-IN", {
          day: "2-digit",
          month: "short",
          year: "numeric",
        }),
};
