import type {
  BondDetail,
  BondsResponse,
  Filters,
  ScreenerParams,
  Status,
  TradesResponse,
} from "./types";

async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "" && v !== false) {
      qs.set(k, String(v));
    }
  }
  const url = qs.size ? `${path}?${qs}` : path;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  bonds: (params: ScreenerParams) =>
    get<BondsResponse>("/api/bonds", params as Record<string, unknown>),
  bond: (isin: string, price?: number) =>
    get<BondDetail>(`/api/bonds/${isin}`, price ? { price } : undefined),
  trades: (
    isin: string,
    params?: { from?: string; to?: string; exchange?: string; segment?: string },
  ) => get<TradesResponse>(`/api/bonds/${isin}/trades`, params),
  status: () => get<Status>("/api/status"),
  filters: () => get<Filters>("/api/filters"),
  watch: async (isin: string) => {
    const res = await fetch(`/api/watchlist/${isin}`, { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
  },
  unwatch: async (isin: string) => {
    const res = await fetch(`/api/watchlist/${isin}`, { method: "DELETE" });
    if (!res.ok) throw new Error(await res.text());
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
  date: (v: string | null | undefined): string =>
    v == null
      ? "–"
      : new Date(v).toLocaleDateString("en-IN", {
          day: "2-digit",
          month: "short",
          year: "numeric",
        }),
};
