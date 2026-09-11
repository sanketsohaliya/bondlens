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

export type VerdictLink = { label: string; href: string };
export type Verdict = { text: string; tone: "ok" | "attention" | "muted"; links: VerdictLink[] };

const exDates = (f: EvidenceFieldComparison) => (f.exchange_dates ?? []).map(calendarDate);

function sourceLink(s: FlowSourceEvidence, notices: ExchangeNotice[]): string | null {
  return (
    notices
      .filter((n) => s.notice_ids.includes(n.id))
      .flatMap((n) => n.links)
      .find(validLink) ?? null
  );
}

/** Signature of a source's disagreements: which fields differ and to what. Two
 * sources with the same signature are saying the same thing against NSDL. */
function differenceSignature(s: FlowSourceEvidence): string | null {
  if (s.state === "conflicting" || s.fields.some((f) => f.state === "conflicting")) return null;
  const differing = s.fields.filter((f) => f.field !== "amount" && f.state === "different");
  if (!differing.length) return null;
  return differing
    .map((f) => `${f.field}=${[...(f.exchange_dates ?? [])].sort().join("/")}>${f.nsdl_date}`)
    .sort()
    .join("|");
}

export function verdictFor(sources: FlowSourceEvidence[], notices: ExchangeNotice[]): Verdict[] {
  const out: Verdict[] = [];
  const consumed = new Set<number>();
  const bySignature = new Map<string, number[]>();
  sources.forEach((s, i) => {
    const sig = differenceSignature(s);
    if (sig) bySignature.set(sig, [...(bySignature.get(sig) ?? []), i]);
  });
  for (const idxs of bySignature.values()) {
    if (idxs.length < 2) continue;
    const group = idxs.map((i) => sources[i]);
    const names = SOURCE_ORDER.filter((n) => group.some((s) => s.source === n));
    const differing = group[0].fields.filter(
      (f) => f.field !== "amount" && f.state === "different",
    );
    const clauses = differing.map(
      (f) =>
        `${FIELD_NAMES[f.field]} ${exDates(f).join(" / ")} — NSDL has ${calendarDate(f.nsdl_date)}`,
    );
    const links: VerdictLink[] = [];
    for (const name of names) {
      const s = group.find((x) => x.source === name);
      const href = s && sourceLink(s, notices);
      if (href) links.push({ label: name, href });
    }
    out.push({
      text: `${names.join(" and ")} both say ${clauses.join("; ")}`,
      tone: "attention",
      links,
    });
    for (const i of idxs) consumed.add(i);
  }
  sources.forEach((s, i) => {
    if (consumed.has(i)) return;
    out.push(singleVerdict(s, notices));
  });
  return out;
}

function singleVerdict(s: FlowSourceEvidence, notices: ExchangeNotice[]): Verdict {
  {
    const dated = s.fields.filter((f) => f.field !== "amount");
    const href = sourceLink(s, notices);
    const links: VerdictLink[] = href ? [{ label: "source", href }] : [];
    const conflicting = dated.filter((f) => f.state === "conflicting");
    if (s.state === "conflicting" || conflicting.length) {
      const detail = conflicting
        .map((f) => `${FIELD_NAMES[f.field]} (${exDates(f).join(" / ")})`)
        .join("; ");
      return {
        text: `${s.source} filings disagree with each other${detail ? ` on ${detail}` : ""} — no version treated as authoritative`,
        tone: "attention",
        links,
      };
    }
    const differing = dated.filter((f) => f.state === "different");
    if (differing.length) {
      const clauses = differing.map(
        (f) =>
          `${FIELD_NAMES[f.field]} ${exDates(f).join(" / ")} — NSDL has ${calendarDate(f.nsdl_date)}`,
      );
      return { text: `${s.source} says ${clauses.join("; ")}`, tone: "attention", links };
    }
    const agreeing = dated.filter((f) => f.state === "agree");
    if (agreeing.length) {
      const parts = agreeing.map((f) => `${FIELD_NAMES[f.field]} ${calendarDate(f.nsdl_date)}`);
      return { text: `${s.source} confirms ${parts.join(" and ")}`, tone: "ok", links };
    }
    return {
      text: `${s.source} notice matched, but supplied no comparable dates`,
      tone: "muted",
      links,
    };
  }
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

/** The date an orphan notice is "about", for the Announced column: the
 * exchange's ex-date, else record date, else due date, else the IST calendar
 * day it was filed. */
export function noticeAnnouncedDate(
  n: Pick<ExchangeNotice, "ex_date" | "record_date" | "due_date" | "published_at">,
): string | null {
  const dated = n.ex_date ?? n.record_date ?? n.due_date;
  if (dated) return dated;
  if (!n.published_at) return null;
  const parsed = new Date(n.published_at);
  return Number.isFinite(parsed.getTime()) ? calendarAsOf(undefined, parsed) : null;
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
