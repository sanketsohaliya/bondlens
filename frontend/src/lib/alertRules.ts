// Turning the Explore filter bar into an alert rule, and describing rules.
//
// A rule watches live asks, so only filters that describe the BOND travel
// with it (rating, maturity, sector, payout...). Filters that describe the
// last TRADE (Min LTP YTM, Retail turnover, Last traded, Below par, Max
// price) are dropped - a never-traded bond with a great resting ask is the
// whole point of an alert. The API refuses them too; this keeps the dialog
// honest about what it saved.

import type { ExploreParams, OpportunityHit } from "../api/types";

export const STATIC_FILTER_KEYS = [
  "min_rating",
  "rating",
  "min_years",
  "max_years",
  "sector",
  "industry",
  "issuer",
  "exchange",
  "frequency",
  "payout",
  "secured_only",
  "unsecured_only",
  "tax_free_only",
  "downgraded_only",
  "exact_only",
] as const;

export const TRADE_HISTORY_LABELS: Record<string, string> = {
  min_ytm: "Min LTP YTM",
  max_ytm: "Max LTP YTM",
  min_turnover: "Retail turnover",
  min_otc_turnover: "OTC turnover",
  traded_within: "Last traded",
  fresh_only: "Fresh prices",
  below_par: "Below face value",
  max_ltp: "Max price (LTP)",
  retail_only: "Trades retail",
  otc_only: "Trades OTC",
};

const isSet = (v: unknown) => v !== undefined && v !== null && v !== "" && v !== false;

/** Filters a rule keeps, and the human labels of the ones it drops. */
export function splitFilters(params: ExploreParams): {
  kept: Record<string, unknown>;
  dropped: string[];
} {
  const kept: Record<string, unknown> = {};
  const dropped: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (!isSet(v) || k === "q" || k === "isins" || k === "sort" || k === "dir") continue;
    if ((STATIC_FILTER_KEYS as readonly string[]).includes(k)) kept[k] = v;
    else if (k in TRADE_HISTORY_LABELS) dropped.push(TRADE_HISTORY_LABELS[k]);
  }
  return { kept, dropped };
}

const inrShort = (v: number): string => {
  if (v >= 1e7) return `₹${(v / 1e7).toFixed(1).replace(/\.0$/, "")} Cr`;
  if (v >= 1e5) return `₹${(v / 1e5).toFixed(1).replace(/\.0$/, "")} L`;
  return `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
};
const tenor = (y: number): string =>
  y < 1 ? `${Math.round(y * 12)}mo` : `${String(Number(y.toFixed(1)))}y`;

/** Client-side mirror of the backend's rule_label(), used only to preview
 * the label in the Save dialog before the rule exists. The server's label
 * is the one shown everywhere afterwards. Keep the two in step. */
export function previewLabel(rule: {
  filters: Record<string, unknown>;
  min_ask_ytm?: number | null;
  max_price?: number | null;
  max_ticket?: number | null;
  min_qty?: number | null;
}): string {
  const parts: string[] = [];
  if (rule.min_ask_ytm != null) parts.push(`Ask YTM ≥ ${rule.min_ask_ytm}%`);
  if (rule.max_price != null) parts.push(`Ask price ≤ ${inrShort(rule.max_price)}`);
  if (rule.max_ticket != null) parts.push(`Ticket ≤ ${inrShort(rule.max_ticket)}`);
  if (rule.min_qty != null && rule.min_qty > 1) parts.push(`Units ≥ ${rule.min_qty}`);
  const f = rule.filters as Partial<ExploreParams> & Record<string, unknown>;
  if (f.min_rating) parts.push(`Rating ≥ ${String(f.min_rating).toUpperCase()}`);
  if (f.rating) parts.push(`Rating ~ ${String(f.rating)}`);
  if (f.min_years != null && f.max_years != null)
    parts.push(`Maturity ${tenor(f.min_years)}–${tenor(f.max_years)}`);
  else if (f.min_years != null) parts.push(`Maturity ≥ ${tenor(f.min_years)}`);
  else if (f.max_years != null) parts.push(`Maturity ≤ ${tenor(f.max_years)}`);
  for (const [key, label] of [
    ["sector", "Sector"],
    ["industry", "Industry"],
    ["payout", "Payout"],
    ["frequency", "Frequency"],
    ["exchange", "Exchange"],
  ] as const)
    if (f[key]) parts.push(`${label}: ${String(f[key])}`);
  if (f.issuer) parts.push(`Issuer ~ ${String(f.issuer)}`);
  if (f.secured_only) parts.push("Secured");
  if (f.unsecured_only) parts.push("Unsecured");
  if (f.tax_free_only) parts.push("Tax-free");
  if (f.downgraded_only) parts.push("Downgraded recently");
  return parts.length ? parts.join(", ") : "Any ask";
}

/** Parse the `?bond=ISIN&tab=` deep link an alert push opens. */
export function parseDeepLink(search: string): { isin: string; tab?: string } | null {
  const qs = new URLSearchParams(search);
  const isin = qs.get("bond")?.trim().toUpperCase();
  if (!isin || !/^[A-Z0-9]{12}$/.test(isin)) return null;
  const tab = qs.get("tab")?.trim() || undefined;
  return { isin, tab };
}

/** One row per bond per trading day: the latest quote plus how many times
 * it fired and the best yield seen. Newest group first. */
export type HitGroup = {
  key: string;
  isin: string;
  name: string;
  side: "ask" | "bid";
  latest: OpportunityHit;
  count: number;
  bestYtm: number | null;
  short: boolean;
};

export function groupHits(hits: OpportunityHit[]): HitGroup[] {
  const groups = new Map<string, HitGroup>();
  const sorted = [...hits].sort((a, b) => b.fired_at.localeCompare(a.fired_at));
  for (const h of sorted) {
    const key = `${h.isin}:${h.trade_day}:${h.side}`;
    const g = groups.get(key);
    const ytm = h.ytm ?? null;
    if (!g) {
      groups.set(key, {
        key,
        isin: h.isin,
        name: h.issuer_name ?? h.nse_symbol ?? h.bse_scrip_id ?? h.isin,
        side: h.side,
        latest: h,
        count: 1,
        bestYtm: ytm,
        short: h.below_threshold != null,
      });
      continue;
    }
    g.count += 1;
    if (
      ytm != null &&
      (g.bestYtm == null || (h.side === "ask" ? ytm > g.bestYtm : ytm < g.bestYtm))
    )
      g.bestYtm = ytm;
    g.short = g.short && h.below_threshold != null;
  }
  return [...groups.values()];
}

/** Hit groups bucketed under the rule label they fired for, newest rule
 * activity first; within a rule, newest bond first. Day is kept on each
 * bucket so an "all days" view can label older sections. */
export type RuleBucket = { key: string; label: string; day: string; groups: HitGroup[] };

export function groupHitsByRule(hits: OpportunityHit[]): RuleBucket[] {
  const buckets = new Map<string, RuleBucket>();
  for (const g of groupHits(hits)) {
    const label = g.latest.rule_label ?? g.latest.source;
    const key = `${g.latest.trade_day}:${label}`;
    const b = buckets.get(key);
    if (b) b.groups.push(g);
    else buckets.set(key, { key, label, day: g.latest.trade_day, groups: [g] });
  }
  return [...buckets.values()];
}

export type WatcherStatus = { label: string; detail?: string; tone: "live" | "idle" | "warn" };

/** One-line status for the panel header from the heartbeat row. */
export function watcherStatus(
  watcher: Record<string, unknown> | null | undefined,
  rulesCount: number,
  kiteConnected: boolean,
  now: number = Date.now(),
): WatcherStatus {
  if (!watcher) return { label: "watcher not reporting", tone: rulesCount ? "warn" : "idle" };
  const beatAge = now - new Date(String(watcher.beat_at)).getTime();
  if (beatAge > 5 * 60_000) return { label: "watcher silent", tone: "warn" };
  const note = String(watcher.note ?? "");
  if (!note.startsWith("open") && !note.startsWith("solving"))
    return { label: "idle until 09:00", tone: "idle" };
  if (rulesCount > 0 && !kiteConnected) return { label: "Kite not connected", tone: "warn" };
  if (note.startsWith("solving")) return { label: "arming new rule…", tone: "live" };
  const n = Number(watcher.tokens ?? 0);
  return { label: "live", detail: `${n.toLocaleString("en-IN")} instruments`, tone: "live" };
}
