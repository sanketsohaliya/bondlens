import type {
  Cashflow,
  CashflowEvidenceResponse,
  EvidenceFieldComparison,
  ExchangeNotice,
  FlowEvidence,
  FlowSourceEvidence,
} from "../api/types";

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

export function excludedReason(flow: Cashflow, asOf: string): string | null {
  if (flow.future || (flow.payment_date ?? flow.due_date) < asOf) return null;
  return flow.record_date && flow.record_date <= asOf
    ? "Record date has passed — a buyer today would not receive this payment; existing holders still may."
    : "A buyer today would not receive this payment under the current entitlement model; existing holders still may.";
}

export function validLink(value: string): boolean {
  if (
    /[\s\\]/.test(value) ||
    [...value].some((c) => c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127)
  )
    return false;
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password;
  } catch {
    return false;
  }
}

const SOURCE_ORDER: Array<FlowSourceEvidence["source"]> = ["NSE", "BSE"];

export function sourceChip(
  row: FlowEvidence | undefined,
): { text: string; attention: boolean } | null {
  const attention = rowAttention(row);
  if (attention === "none" || !row) return null;
  const names = SOURCE_ORDER.filter((s) => row.sources.some((x) => x.source === s));
  return { text: names.join("·"), attention: attention === "attention" };
}

const FIELD_NAMES: Record<EvidenceFieldComparison["field"], string> = {
  due_date: "due",
  record_date: "record date",
  payment_date: "payment date",
  amount: "amount",
};

export type Verdict = { text: string; tone: "ok" | "attention" | "muted"; link: string | null };

const exDates = (f: EvidenceFieldComparison) => (f.exchange_dates ?? []).map(calendarDate);

export function verdictFor(sources: FlowSourceEvidence[], notices: ExchangeNotice[]): Verdict[] {
  return sources.map((s) => {
    const dated = s.fields.filter((f) => f.field !== "amount");
    const link =
      notices
        .filter((n) => s.notice_ids.includes(n.id))
        .flatMap((n) => n.links)
        .find(validLink) ?? null;
    const conflicting = dated.filter((f) => f.state === "conflicting");
    if (s.state === "conflicting" || conflicting.length) {
      const detail = conflicting
        .map((f) => `${FIELD_NAMES[f.field]} (${exDates(f).join(" / ")})`)
        .join("; ");
      return {
        text: `${s.source} filings disagree with each other${detail ? ` on ${detail}` : ""} — no version treated as authoritative`,
        tone: "attention",
        link,
      };
    }
    const differing = dated.filter((f) => f.state === "different");
    if (differing.length) {
      const clauses = differing.map(
        (f) =>
          `${FIELD_NAMES[f.field]} ${exDates(f).join(" / ")} — NSDL has ${calendarDate(f.nsdl_date)}`,
      );
      return { text: `${s.source} says ${clauses.join("; ")}`, tone: "attention", link };
    }
    const agreeing = dated.filter((f) => f.state === "agree");
    if (agreeing.length) {
      const parts = agreeing.map((f) => `${FIELD_NAMES[f.field]} ${calendarDate(f.nsdl_date)}`);
      return { text: `${s.source} confirms ${parts.join(" and ")}`, tone: "ok", link };
    }
    return {
      text: `${s.source} notice matched, but supplied no comparable dates`,
      tone: "muted",
      link,
    };
  });
}

export function calendarDateTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return null;
  const date = parsed.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  });
  const time = parsed.toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Kolkata",
  });
  return `${date}, ${time} IST`;
}

export type NoticeFacts = {
  header: string;
  quote: string | null;
  dates: Array<{ label: string; value: string; title?: string }>;
  time: string | null;
};

const EX_DATE_TITLE =
  "Exchange ex-date: trades from this date settle without the payment. It is not a payment date.";

export function noticeFacts(n: ExchangeNotice): NoticeFacts {
  const header = (n.category || n.nature || "Captured notice").trim();
  const headline = (n.headline ?? "").trim();
  const quote = headline && headline.toLowerCase() !== header.toLowerCase() ? headline : null;
  const dates: NoticeFacts["dates"] = [];
  if (n.ex_date)
    dates.push({ label: "Ex-date", value: calendarDate(n.ex_date), title: EX_DATE_TITLE });
  if (n.record_date) dates.push({ label: "Record date", value: calendarDate(n.record_date) });
  if (n.due_date) dates.push({ label: "Due", value: calendarDate(n.due_date) });
  if (n.reported_payment_date)
    dates.push({
      label: "Payment",
      value: calendarDate(n.reported_payment_date),
      title: "Payment date as reported by the issuer; not confirmation of bank credit.",
    });
  const filed = calendarDateTime(n.published_at);
  const captured = calendarDateTime(n.first_captured_at);
  const time = filed ? `Filed ${filed}` : captured ? `Captured ${captured}` : null;
  return { header, quote, dates, time };
}

export function rowAttention(row: FlowEvidence | undefined): "none" | "info" | "attention" {
  if (!row?.sources.length) return "none";
  const attention = row.sources.some(
    (s) =>
      s.state === "conflicting" ||
      s.fields.some((f) => f.state === "different" || f.state === "conflicting"),
  );
  return attention ? "attention" : "info";
}
