import { useEffect, useState } from "react";
import { fmt } from "../api/client";
import type { YieldValuation, YieldWarning } from "../api/types";
import { calendarAsOf } from "./cashflowEvidence";

export function isQuoteFresh(
  quote: { fetched_at?: string | null; expires_at?: string | null } | null | undefined,
  now = Date.now(),
): boolean {
  const fetched = Date.parse(quote?.fetched_at ?? "");
  const expires = Date.parse(quote?.expires_at ?? "");
  return (
    Number.isFinite(fetched) && fetched <= now + 30_000 && now - fetched < 90_000 && expires > now
  );
}

export function valuationIsCurrent(
  value: YieldValuation | null | undefined,
  now = Date.now(),
): boolean {
  const calculated = Date.parse(value?.calculated_at ?? "");
  return (
    Boolean(value?.calculation_version) &&
    value?.as_of === calendarAsOf(undefined, new Date(now)) &&
    Number.isFinite(calculated) &&
    calculated <= now + 30_000 &&
    now - calculated < 90_000
  );
}

export function valuationLabel(value: YieldValuation | null | undefined): string {
  const kind = value?.yield_type === "ytc" || value?.ytm_method === "ytc" ? "YTC" : "YTM";
  if (value?.ytm_method === "approx") return `${kind} (approx)`;
  return value?.estimated || value?.ytm_method === "estimated" ? `${kind} (estimated)` : kind;
}

export function isMarketComparison(note: Pick<YieldWarning, "code">): boolean {
  return note.code === "otc_difference";
}

export function yieldReviewWarnings(value: YieldValuation | null | undefined): YieldWarning[] {
  return (value?.warnings ?? []).filter((note) => !isMarketComparison(note));
}

export function hasYieldReview(
  value: YieldValuation | null | undefined,
  reliable = value?.ytm_reliable,
): boolean {
  return yieldReviewWarnings(value).length > 0 || (!value?.warnings?.length && reliable === false);
}

/** Mirror of backend valuation.OTC_MAX_GAP / OTC_MAX_AGE (backend/analytics/valuation.py). */
export const OTC_MAX_GAP = 3.0;
export const OTC_MAX_AGE_DAYS = 14;

/** Magnitude for prose: whole points once the gap is large, one decimal below that. */
export function gapText(gap: number): string {
  const g = Math.abs(gap);
  return g >= 10 ? g.toFixed(0) : g.toFixed(1);
}

/** The OTC comparison in the units the reader is already looking at: what
 * institutions paid per unit versus the retail last trade, as a rupee gap.
 * Null when the print could not be expressed in rupees (accrued interest
 * unknown), in which case callers fall back to the yield headline. */
export function otcJudgement(
  note: Pick<
    YieldWarning,
    "otc_price" | "retail_price" | "price_gap" | "price_gap_pct" | "otc_trade_date"
  >,
): string | null {
  const { otc_price: otc, retail_price: retail, price_gap: gap, price_gap_pct: pct } = note;
  if (otc == null || retail == null || gap == null || pct == null) return null;
  const when = note.otc_trade_date ? ` on ${fmt.date(note.otc_trade_date)}` : "";
  const word = gap < 0 ? "cheaper" : "more";
  const whole = (v: number) => fmt.inrFull(Math.round(v));
  return `Institutions paid ≈${whole(otc)} for this${when}; retail last traded at ${whole(retail)} — ${whole(Math.abs(gap))} (${Math.abs(pct).toFixed(1)}%) ${word}.`;
}

/** Yield-only fallback when no rupee comparison is available. */
export function yieldGapText(gap: number): string {
  const direction = gap > 0 ? "above" : "below";
  return `Retail yield is ${gapText(gap)} pts ${direction} the recent OTC print.`;
}

export type ComparisonText = {
  headline: string;
  action: string | null;
  tone: "attention" | "neutral";
};

/** Decision-oriented wording for an otc_difference note. Direction matters:
 * retail yield ABOVE OTC means the retail price is below institutional (a
 * stale or thin print, or a genuine bargain); BELOW means you would pay more
 * than institutions did. Falls back to the stored sentence for old snapshots
 * that predate the structured fields. */
export function comparisonText(
  note: YieldWarning,
  retailYtm: number | null | undefined,
): ComparisonText {
  const { otc_yield: otc, otc_trade_date: date, gap } = note;
  if (otc == null || gap == null || retailYtm == null)
    return { headline: note.message, action: null, tone: "neutral" };
  const direction = gap > 0 ? "above" : "below";
  const when = date ? ` (${fmt.date(date)})` : "";
  const headline = `Retail ${fmt.pct(retailYtm)} vs OTC ${fmt.pct(otc)}${when} — ${Math.abs(gap).toFixed(1)} pts ${direction} institutional.`;
  return { headline, action: otcJudgement(note), tone: "attention" };
}

const SHORT_CAVEATS: Record<string, string> = {
  call_scenario: "Yield to call — assumes the issuer exercises the call at par.",
  variable_coupon: "Floating coupon — yield assumes today's rate.",
  yield_range: "Yield is outside the sanity range — verify the price.",
  short_horizon_range: "Very short horizon — check the rupee gain, not the annualised rate.",
  future_otc_date: "OTC print is future-dated — comparison unavailable.",
  estimated_payments: "Some payments are estimated from the coupon rate.",
  schedule_invalid: "Payment schedule could not be validated.",
};
const CAVEAT_PRIORITY = [
  "otc_difference",
  "schedule_invalid",
  "yield_range",
  "short_horizon_range",
  "call_scenario",
  "variable_coupon",
  "estimated_payments",
];

/** Hover tooltip: what you would earn, plus at most one caveat. Everything
 * else about how the number was produced lives in provenanceLines(). */
export function valuationTooltip(value: YieldValuation | null | undefined): string {
  if (!value)
    return "Calculation metadata unavailable for this snapshot; refresh the valuation before relying on it.";
  const horizon =
    value.yield_type === "ytc" || value.ytm_method === "ytc" ? "first call" : "maturity";
  const lines =
    value.gross_gain != null
      ? [
          `Earn ${fmt.inrFull(value.gross_gain)} per unit (${fmt.pct(value.holding_period_return)}) if held to ${horizon}, before tax.`,
        ]
      : [`Indicative ${valuationLabel(value)} at the displayed price.`];
  const warnings = value.warnings ?? [];
  for (const code of CAVEAT_PRIORITY) {
    const note = warnings.find((w) => w.code === code);
    if (!note) continue;
    if (code === "otc_difference" && note.gap != null)
      lines.push(`⚠ ${otcJudgement(note) ?? yieldGapText(note.gap)}`);
    else lines.push(`⚠ ${SHORT_CAVEATS[code] ?? note.message}`);
    break;
  }
  return lines.join("\n");
}

/** Full calculation provenance, for the drawer's ⓘ beside the yield. */
export function provenanceLines(value: YieldValuation | null | undefined): string[] {
  if (!value) return [];
  const lines: string[] = [];
  if (value.as_of) lines.push(`Valuation date: ${value.as_of}.`);
  if (value.price_basis) lines.push(value.price_basis);
  if (value.periods_per_year)
    lines.push(`Nominal annual rate, compounded ${value.periods_per_year} times/year.`);
  if (value.day_count)
    lines.push(
      `Day count: ${value.day_count === "act/act" ? "Actual/Actual calendar-year ISDA (model assumption)" : value.day_count}.`,
    );
  if (value.gross_gain != null)
    lines.push(
      `Total modeled cash gain ${fmt.inrFull(value.gross_gain)} per unit (${fmt.pct(value.holding_period_return)} of price), without reinvestment, fees or taxes.`,
    );
  for (const note of value.warnings ?? []) if (!isMarketComparison(note)) lines.push(note.message);
  if (value.settlement_assumption) lines.push(value.settlement_assumption);
  if (value.calculation_version) lines.push(`Calculator: ${value.calculation_version}.`);
  return lines;
}

export function useQuoteClock(): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const update = () => setNow(Date.now());
    const timer = setInterval(update, 5000);
    window.addEventListener("focus", update);
    document.addEventListener("visibilitychange", update);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", update);
      document.removeEventListener("visibilitychange", update);
    };
  }, []);
  return now;
}
