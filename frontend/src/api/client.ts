import type {
  BondDetail,
  BondsResponse,
  Filters,
  ListName,
  ListsResponse,
  ScreenerParams,
  Status,
  TradesResponse,
  User,
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
  auth: {
    google: (credential: string) => request<User>("POST", "/api/auth/google", { credential }),
    me: () => get<User>("/api/auth/me"),
    logout: () => request<{ ok: boolean }>("POST", "/api/auth/logout"),
  },
  lists: {
    get: () => get<ListsResponse>("/api/lists"),
    add: (list: ListName, isin: string) => request<unknown>("PUT", `/api/lists/${list}/${isin}`),
    remove: (list: ListName, isin: string) =>
      request<unknown>("DELETE", `/api/lists/${list}/${isin}`),
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
