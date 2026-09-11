import type { Cashflow, CashflowEvidenceResponse } from "../api/types";

type FlowIdentity = Pick<Cashflow, "event" | "due_date" | "record_date" | "payment_date">;

export function flowKey(flow: FlowIdentity): string {
  return [flow.event, flow.due_date, flow.record_date ?? "", flow.payment_date ?? ""].join("|");
}

export function isCalendarDate(value: string | null | undefined): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value) || Number(value.slice(0, 4)) >= 2500)
    return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

export function calendarAsOf(value: string | null | undefined, now = new Date()): string {
  if (isCalendarDate(value)) return value;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const part = (kind: string) => parts.find((p) => p.type === kind)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

export function calendarDate(value: string | null | undefined): string {
  return isCalendarDate(value)
    ? new Date(`${value}T00:00:00Z`).toLocaleDateString("en-IN", {
        day: "2-digit",
        month: "short",
        year: "numeric",
        timeZone: "UTC",
      })
    : "–";
}

export function splitCashflows(flows: Cashflow[], asOf: string) {
  const upcoming: Cashflow[] = [];
  const past: Cashflow[] = [];
  const undated: Cashflow[] = [];
  for (const flow of flows) {
    const date = flow.payment_date ?? flow.due_date;
    if (!isCalendarDate(date)) undated.push(flow);
    else if (date >= asOf) upcoming.push(flow);
    else past.push(flow);
  }
  const compare = (a: Cashflow, b: Cashflow) =>
    (a.payment_date ?? a.due_date).localeCompare(b.payment_date ?? b.due_date) ||
    a.event.localeCompare(b.event);
  return { upcoming: upcoming.sort(compare), past: past.sort(compare).reverse(), undated };
}

export function evidenceMatchesSchedule(
  isin: string,
  asOf: string,
  flows: Cashflow[],
  evidence: CashflowEvidenceResponse | undefined,
): evidence is CashflowEvidenceResponse {
  if (!evidence || evidence.isin !== isin || evidence.as_of !== asOf) return false;
  const current = new Map<string, number>();
  for (const flow of flows) {
    const key = flowKey(flow);
    current.set(key, (current.get(key) ?? 0) + 1);
  }
  const returned = new Set(evidence.flows.map((f) => f.key));
  return (
    current.size === returned.size &&
    returned.size === evidence.flows.length &&
    evidence.flows.every(
      (f) =>
        f.key === flowKey(f) && current.has(f.key) && (current.get(f.key) ?? 0) > 1 === f.ambiguous,
    )
  );
}

export function buyerEligibility(flow: Cashflow, asOf: string): string | null {
  if (flow.future || (flow.payment_date ?? flow.due_date) < asOf) return null;
  return flow.record_date && flow.record_date <= asOf
    ? "Record date passed; excluded for a new buyer under the current model. This may still be due to existing holders."
    : "Scheduled today or otherwise excluded for a new buyer under the current entitlement model; this is not proof of payment.";
}
