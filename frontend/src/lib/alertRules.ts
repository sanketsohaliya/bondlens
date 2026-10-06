// Turning the Explore filter bar into an alert rule, and describing rules.
//
// A rule watches live asks, so only filters that describe the BOND travel
// with it (rating, maturity, sector, payout...). Filters that describe the
// last TRADE (Min LTP YTM, Retail turnover, Last traded, Below par, Max
// price) are dropped - a never-traded bond with a great resting ask is the
// whole point of an alert. The API refuses them too; this keeps the dialog
// honest about what it saved.

import { fmt } from "../api/client";
import type { AlertRule, ExploreParams } from "../api/types";

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

/** "rating ≥ BBB- · maturity ≥ 0.5y · bullet" for a rule's filters. */
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
  return parts.length ? parts.join(" · ") : "every listed bond";
}

/** "YTM @ Ask ≥ 12% · ticket ≤ ₹2.00 L · price ≤ ₹1,050" */
export function describeConditions(
  rule: Pick<AlertRule, "min_ask_ytm" | "max_ticket" | "max_price" | "min_qty">,
): string {
  const parts: string[] = [];
  if (rule.min_ask_ytm != null) parts.push(`YTM @ Ask ≥ ${rule.min_ask_ytm}%`);
  if (rule.max_price != null) parts.push(`price ≤ ${fmt.inrFull(rule.max_price)}`);
  if (rule.max_ticket != null) parts.push(`ticket ≤ ${fmt.inr(rule.max_ticket)}`);
  if (rule.min_qty != null && rule.min_qty > 1) parts.push(`≥ ${rule.min_qty} units`);
  return parts.join(" · ");
}

/** Short tokens for a rule name: "BBB-", "1y+", "≤3y", "bullet", "on NSE". */
export function compactFilterTokens(filters: Record<string, unknown>): string[] {
  const f = filters as Partial<ExploreParams> & Record<string, unknown>;
  const out: string[] = [];
  if (f.min_rating) out.push(String(f.min_rating));
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

/** A default rule name, "BBB-, 1y+, ask ≥ 12%", capped at the API's 60 chars. */
export function suggestRuleName(
  filters: Record<string, unknown>,
  minYtm: string,
  maxPrice = "",
): string {
  const tokens = compactFilterTokens(filters);
  if (minYtm) tokens.push(`ask ≥ ${minYtm}%`);
  else if (maxPrice) tokens.push(`price ≤ ₹${Number(maxPrice).toLocaleString("en-IN")}`);
  const name = tokens.length ? tokens.join(", ") : "all bonds";
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
