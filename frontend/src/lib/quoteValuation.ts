import { useEffect, useState } from "react";
import { fmt } from "../api/client";
import type { YieldValuation } from "../api/types";
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

export function valuationTooltip(value: YieldValuation | null | undefined): string {
  if (!value)
    return "Calculation metadata unavailable for this snapshot; refresh the valuation before relying on it.";
  const lines = [
    `Indicative gross ${valuationLabel(value)} at the displayed price; not a guaranteed return.`,
  ];
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
      `Total modeled cash gain: ${fmt.inrFull(value.gross_gain)} per unit (${fmt.pct(value.holding_period_return)} of price), without reinvestment, fees or taxes.`,
    );
  lines.push(...(value.warnings ?? []).map((warning) => warning.message));
  if (value.settlement_assumption) lines.push(value.settlement_assumption);
  if (value.calculation_version) lines.push(`Calculator: ${value.calculation_version}.`);
  return lines.join("\n");
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
