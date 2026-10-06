// Turning the Explore filter bar into an alert rule, and describing rules.
//
// A rule watches live asks, so only filters that describe the BOND travel
// with it (rating, maturity, sector, payout...). Filters that describe the
// last TRADE (Min LTP YTM, Retail turnover, Last traded, Below par, Max
// price) are dropped - a never-traded bond with a great resting ask is the
// whole point of an alert. The API refuses them too; this keeps the dialog
// honest about what it saved.

import { fmt } from "../api/client";
import type { AlertRule, ExploreParams, OpportunityHit } from "../api/types";

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

/** "rating ≥ BBB-, maturity ≥ 0.5y, bullet" for a rule's filters. */
export function describeRuleFilters(filters: Record<string, unknown>): string {
  const parts: string[] = [];
  const f = filters as Partial<ExploreParams> & Record<string, unknown>;
  if (f.min_rating) parts.push(`rating ≥ ${f.min_rating}`);
  if (f.rating) parts.push(`rating ~ ${String(f.rating)}`);
  if (f.min_years != null) parts.push(`maturity ≥ ${f.min_years}y`);
  if (f.max_years != null) parts.push(`maturity ≤ ${f.max_years}y`);
  if (f.sector) parts.push(String(f.sector));
  if (f.industry) parts.push(String(f.industry));
  if (f.issuer) parts.push(`issuer ~ ${String(f.issuer)}`);
  if (f.exchange) parts.push(`on ${f.exchange}`);
  if (f.frequency) parts.push(`pays ${f.frequency}`);
  if (f.payout) parts.push(String(f.payout));
  if (f.secured_only) parts.push("secured");
  if (f.unsecured_only) parts.push("unsecured");
  if (f.tax_free_only) parts.push("tax-free");
  if (f.downgraded_only) parts.push("downgraded recently");
  return parts.length ? parts.join(", ") : "every listed bond";
}

/** "YTM @ Ask ≥ 12%, ticket ≤ ₹2.00 L, price ≤ ₹1,050" */
export function describeConditions(
  rule: Pick<AlertRule, "min_ask_ytm" | "max_ticket" | "max_price" | "min_qty">,
): string {
  const parts: string[] = [];
  if (rule.min_ask_ytm != null) parts.push(`YTM @ Ask ≥ ${rule.min_ask_ytm}%`);
  if (rule.max_price != null) parts.push(`price ≤ ${fmt.inrFull(rule.max_price)}`);
  if (rule.max_ticket != null) parts.push(`ticket ≤ ${fmt.inr(rule.max_ticket)}`);
  if (rule.min_qty != null && rule.min_qty > 1) parts.push(`≥ ${rule.min_qty} units`);
  return parts.join(", ");
}

/** Pill-ready phrases for a rule's filters: "BBB- and above", "6mo+",
 * "1–3y", "≤2y", sector/payout words, "on NSE", "secured"... */
export function compactFilterTokens(filters: Record<string, unknown>): string[] {
  const f = filters as Partial<ExploreParams> & Record<string, unknown>;
  const out: string[] = [];
  if (f.min_rating) out.push(`${String(f.min_rating)} and above`);
  if (f.rating) out.push(`~${String(f.rating)}`);
  const span = (y: number) => (y < 1 ? `${Math.round(y * 12)}mo` : `${Number(y.toFixed(1))}y`);
  if (f.min_years != null && f.max_years != null)
    out.push(`${span(f.min_years)}–${span(f.max_years)}`);
  else if (f.min_years != null) out.push(`${span(f.min_years)}+`);
  else if (f.max_years != null) out.push(`≤${span(f.max_years)}`);
  for (const key of ["sector", "industry", "issuer", "payout", "frequency"] as const)
    if (f[key]) out.push(String(f[key]));
  if (f.exchange) out.push(`on ${f.exchange}`);
  if (f.secured_only) out.push("secured");
  if (f.unsecured_only) out.push("unsecured");
  if (f.tax_free_only) out.push("tax-free");
  if (f.downgraded_only) out.push("downgraded");
  return out;
}

/** "Ask ≥ 15%" / "Ask ≤ ₹1,050" / "Ask ≥ 15%, ≤ ₹1,050" */
export function describeAskLead(minYtm: string, maxPrice = ""): string {
  const parts: string[] = [];
  if (minYtm) parts.push(`≥ ${minYtm}%`);
  if (maxPrice) parts.push(`≤ ₹${Number(maxPrice).toLocaleString("en-IN")}`);
  return parts.length ? `Ask ${parts.join(", ")}` : "Ask";
}

/** A default rule name, "Ask ≥ 15% — BBB- and above, 6mo+", capped at 60 chars. */
export function suggestRuleName(
  filters: Record<string, unknown>,
  minYtm: string,
  maxPrice = "",
): string {
  const tokens = compactFilterTokens(filters);
  const name = `${describeAskLead(minYtm, maxPrice)} — ${tokens.length ? tokens.join(", ") : "any bond"}`;
  return name.length > 60 ? `${name.slice(0, 57)}…` : name;
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
