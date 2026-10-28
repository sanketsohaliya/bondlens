import { useEffect, useState } from "react";
import { fmt } from "../api/client";
import type { YieldValuation, YieldWarning } from "../api/types";
import { calendarAsOf } from "./cashflowEvidence";

/** A valuation is usable while it was computed for today's calendar day
 * with a known calculator. Quotes and valuations are snapshots captured
 * when the page (or the order book) loaded; nothing ages them out - the
 * capture time is shown instead, and a reload captures again. */
export function valuationIsCurrent(
  value: YieldValuation | null | undefined,
  now = Date.now(),
): boolean {
  return (
    Boolean(value?.calculation_version) && value?.as_of === calendarAsOf(undefined, new Date(now))
  );
}

/** "10:31" / "10:31:41" in IST for an ISO timestamp; null when unparsable. */
export function fmtIstTime(iso: string | null | undefined, seconds = false): string | null {
  const t = Date.parse(iso ?? "");
  if (!Number.isFinite(t)) return null;
  return new Date(t).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    ...(seconds ? { second: "2-digit" } : {}),
    hour12: false,
    timeZone: "Asia/Kolkata",
  });
}

export function valuationLabel(value: YieldValuation | null | undefined): string {
  const kind = value?.yield_type === "ytc" || value?.ytm_method === "ytc" ? "YTC" : "YTM";
  if (value?.ytm_method === "approx") return `${kind} (approx)`;
  return value?.estimated || value?.ytm_method === "estimated" ? `${kind} (estimated)` : kind;
}

export function isMarketComparison(note: Pick<YieldWarning, "code">): boolean {
  return note.code === "otc_difference";
}

/** Every warning code the backend can emit, with the one short sentence the
 * hover shows for it. Order within the map is the display priority when a
 * valuation carries several. `null` = the sentence is composed elsewhere
 * (old_price from its day count, otc_difference from the rupee gap).
 *
 * Which of these show the ⚠ badge is NOT decided here: the backend's
 * severity does that (caution/error = the number is suspect; info = context).
 * A CI test asserts every backend code has an entry, so a badge can never
 * appear without an explanation. */
export const CAVEATS: Record<string, string | null> = {
  // error
  schedule_invalid: "Payment schedule could not be validated.",
  maturity_mismatch:
    "Cashflow schedule and stated maturity disagree — the yield is computed on the schedule and may be wrong.",
  invalid_payout: "Modeled payout is not finite — totals withheld.",
  // caution: the calculation or its inputs are suspect
  yield_unavailable: "Yield could not be solved.",
  price_unavailable: "No usable price.",
  yield_range: "Yield is outside the sanity range — verify the price.",
  short_horizon_range: "Very short horizon — check the rupee gain, not the annualised rate.",
  approximation: "Approximate yield — schedule detail was insufficient for an exact solve.",
  call_scenario: "Yield to call — assumes the issuer exercises the call at par.",
  callable_maturity: "Callable before maturity — the yield to maturity may not be realised.",
  unresolved_calls: "Some call terms are unresolved — yield-to-worst unavailable.",
  variable_coupon: "Floating coupon — yield assumes today's rate.",
  estimated_payments: "Some payments are estimated from the coupon rate.",
  future_price_date: "Price is future-dated — verify the source.",
  // info: context, never a badge
  otc_difference: null,
  coupon_inferred: null,
  old_price: null,
  short_horizon: "Matures within 90 days — the annualised yield is sensitive to timing.",
  future_otc_date: "OTC print is future-dated — comparison unavailable.",
};

const SEVERITY_RANK: Record<string, number> = { error: 0, caution: 1, info: 2 };

/** Warnings that cast doubt on the number itself — anything the backend did
 * not mark as mere information. Drives every ⚠ / "review" marker. */
export function yieldReviewWarnings(value: YieldValuation | null | undefined): YieldWarning[] {
  return (value?.warnings ?? []).filter((note) => note.severity !== "info");
}

export function hasYieldReview(
  value: YieldValuation | null | undefined,
  reliable = value?.ytm_reliable,
): boolean {
  return yieldReviewWarnings(value).length > 0 || (!value?.warnings?.length && reliable === false);
}

/** The single caveat sentence for a note, or null if it says nothing useful. */
export function caveatText(note: YieldWarning): string | null {
  if (note.code === "coupon_inferred") {
    const n = Number((note as Record<string, unknown>).count ?? 1);
    const rate = String((note as Record<string, unknown>).rate ?? "the published rate");
    return `${n} coupon${n === 1 ? "" : "s"} NSDL omitted on repayment date${n === 1 ? "" : "s"} ${n === 1 ? "was" : "were"} added from the ${rate} rate.`;
  }
  if (note.code === "otc_difference")
    return note.gap != null ? (otcJudgement(note) ?? yieldGapText(note.gap)) : note.message;
  if (note.code === "old_price") {
    const days = note.message.match(/(\d+) days old/)?.[1];
    return days ? `Last trade ${days} days ago — not a current offer.` : note.message;
  }
  // A code we have a sentence for; otherwise the backend's own message so a
  // new code is never silent.
  return CAVEATS[note.code] ?? note.message;
}

/** Pick the one caveat to show: highest severity first, then map order. */
export function primaryCaveat(value: YieldValuation | null | undefined): YieldWarning | null {
  const order = Object.keys(CAVEATS);
  const notes = [...(value?.warnings ?? [])].sort(
    (a, b) =>
      (SEVERITY_RANK[a.severity] ?? 1) - (SEVERITY_RANK[b.severity] ?? 1) ||
      (order.indexOf(a.code) + 1 || 999) - (order.indexOf(b.code) + 1 || 999),
  );
  return notes[0] ?? null;
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
  // One caveat, highest severity first. The glyph matches the badge: ⚠ for
  // a caution/error (the row shows ⚠ too), ⓘ for information (it does not).
  const note = primaryCaveat(value);
  if (note) {
    const text = caveatText(note);
    if (text) lines.push(`${note.severity === "info" ? "ⓘ" : "⚠"} ${text}`);
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
