import type { BondDetail } from "../api/types";
import { calendarAsOf } from "./cashflowEvidence";

type YieldData = Pick<
  BondDetail,
  | "ytm"
  | "ytm_exact"
  | "ytm_approx"
  | "amounts_estimated"
  | "yield_amounts_estimated"
  | "maturity_date"
  | "call_dates"
>;

export function isYieldToCall(bond: YieldData): boolean {
  return (
    bond.maturity_date == null &&
    bond.ytm_exact == null &&
    bond.ytm_approx == null &&
    bond.call_dates.length > 0
  );
}

export function firstFutureCall(bond: Pick<BondDetail, "as_of" | "call_dates">): string | null {
  const today = calendarAsOf(bond.as_of);
  return bond.call_dates.filter((d) => d > today).sort()[0] ?? null;
}

export function yieldLabel(bond: YieldData): string {
  if (bond.ytm == null) return "Yield unavailable";
  if (isYieldToCall(bond)) return "YTC";
  if (bond.ytm_exact != null)
    return (bond.yield_amounts_estimated ?? bond.amounts_estimated)
      ? "YTM (estimated)"
      : "YTM (exact)";
  return "YTM (approx)";
}
