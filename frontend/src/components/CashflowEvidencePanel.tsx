import { fmt } from "../api/client";
import type {
  Cashflow,
  CashflowEvidenceResponse,
  EvidenceAvailability,
  EvidenceFieldComparison,
  ExchangeNotice,
  FlowEvidence,
} from "../api/types";
import { calendarDate, validLink, verdictFor } from "../lib/cashflowEvidence";

const REASONS: Record<string, string> = {
  event_applies_to_another_isin:
    "This filing mentions this bond, but its payment line identifies another ISIN.",
  ambiguous_event_isin: "The payment-applicable ISIN could not be identified unambiguously.",
  identifier_conflict: "The source identifiers disagree; no automatic match was made.",
  competing_versions:
    "Source versions differ in event details or dates; no version was chosen as authoritative.",
  related_version_outside_window:
    "Related conflicting version outside the checked period; shown so its dates can be inspected.",
  multiple_nsdl_candidates: "More than one NSDL flow could match; no amount-based guess was made.",
  partially_matched_notice:
    "Some event components were matched; the remaining components need review.",
  early_redemption_review:
    "Early-redemption entry: review the source. It does not establish a new payment date, price or maturity.",
  purpose_requires_review:
    "The source purpose needs interpretation; it was not converted into a payment.",
  partial_evidence_set: "The evidence set is incomplete; automatic matching is disabled.",
  source_mapping_blocked:
    "The BSE mapping is unresolved; archived evidence is shown for review only.",
  unverified_bse_identity: "No verified BSE instrument association is available.",
  unparsed_payment_clause: "No single structured payment clause could be parsed.",
  unrecognized_nature: "The payment nature is not recognized by the date comparison rules.",
  due_date_not_supplied: "No unambiguous contractual due date was supplied.",
  record_date_not_supplied: "No unambiguous record date was supplied.",
  not_an_upcoming_payment_notice:
    "This is supporting source context, not a parsed upcoming-payment notice.",
};

export function reasonText(reason: string): string {
  return REASONS[reason] ?? reason.replaceAll("_", " ");
}

const AVAILABILITY: Record<EvidenceAvailability["status"], string> = {
  fresh: "Capture current",
  stale: "Capture stale / failed",
  incomplete: "Capture incomplete",
  unavailable: "Capture unavailable",
  mapping_blocked: "Mapping unresolved",
  not_captured: "Not captured",
};

export function SourceAvailability({ sources }: { sources: EvidenceAvailability[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-400">
      {sources.map((s) => (
        <span key={s.source} title={(s.reasons ?? []).map(reasonText).join(" ")}>
          <span className="text-zinc-400">{s.source}</span> · {AVAILABILITY[s.status]}
          {s.last_success_at &&
            ` · checked ${new Date(s.last_success_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })} IST`}
        </span>
      ))}
    </div>
  );
}

const NOTICE_STATES: Record<ExchangeNotice["status"], string> = {
  matched: "Matched notice",
  unmatched: "Not matched to NSDL",
  ambiguous: "Ambiguous match",
  review_only: "Review source",
  context_only: "Related mention only",
};

export function NoticeCard({ notice: n }: { notice: ExchangeNotice }) {
  const links = n.links.filter(validLink);
  const dates = [
    ["Contractual due", n.due_date],
    ["Record date", n.record_date],
    ["Ex-date", n.ex_date],
    ["Issuer-reported payment", n.reported_payment_date],
  ];
  return (
    <article className="rounded-md border border-zinc-800 bg-zinc-950/50 p-3 text-xs whitespace-normal">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-semibold text-zinc-300">{n.source}</span>
        <span className={n.redemption_form === "early" ? "text-amber-400/80" : "text-zinc-400"}>
          {n.category || n.nature || "Captured notice"}
        </span>
        <span className="text-[10px] text-zinc-400">{NOTICE_STATES[n.status]}</span>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        {dates
          .filter(([, value]) => value)
          .map(([label, value]) => (
            <div key={label}>
              <dt className="text-zinc-400">{label}</dt>
              <dd className="text-zinc-300">{calendarDate(value)}</dd>
            </div>
          ))}
      </dl>
      {n.source === "BSE" && !n.due_date && (
        <p className="mt-2 text-[11px] text-zinc-400">
          Payment due date and amount not supplied. Ex-date is not a payment date.
        </p>
      )}
      {n.reasons.length > 0 && (
        <ul className="mt-2 space-y-1 text-[11px] text-zinc-400">
          {n.reasons.map((reason) => (
            <li key={reason}>{reasonText(reason)}</li>
          ))}
        </ul>
      )}
      {n.unmatched_events.length > 0 && (
        <p className="mt-1 text-[11px] text-zinc-400">
          Unmatched components: {n.unmatched_events.join(", ")}
        </p>
      )}
      <details className="mt-2 text-[11px] text-zinc-400">
        <summary className="cursor-pointer hover:text-zinc-300">
          Original notice and provenance
        </summary>
        <p className="mt-2 whitespace-pre-wrap break-words text-zinc-400">
          {n.headline || "No source text captured."}
        </p>
        {n.event_isin && <p className="mt-1 break-all">Payment-line ISIN: {n.event_isin}</p>}
        {n.published_at ? (
          <p className="mt-1">
            Filed on{" "}
            {new Date(n.published_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST
          </p>
        ) : (
          n.first_captured_at && (
            <p className="mt-1">
              First captured{" "}
              {new Date(n.first_captured_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}{" "}
              IST · publication time not supplied
            </p>
          )
        )}
        <p className="mt-1">
          {n.representation_count} stored representation{n.representation_count === 1 ? "" : "s"}{" "}
          (not a payment count).
        </p>
        <p className="break-words">Evidence IDs: {n.raw_ids.join(", ")}</p>
      </details>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
        {links.map((url, i) => (
          <a
            key={url}
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[11px] text-zinc-400 underline decoration-zinc-700 underline-offset-2 hover:text-zinc-100"
          >
            {n.source === "BSE" ? "BSE source" : "Source document"}
            {links.length > 1 ? ` ${i + 1}` : ""}
          </a>
        ))}
        {links.length === 0 && (
          <span className="text-[11px] text-zinc-400">Source link not captured</span>
        )}
      </div>
    </article>
  );
}

const FIELDS: Array<[EvidenceFieldComparison["field"], string]> = [
  ["due_date", "Contractual due date"],
  ["payment_date", "Planned payment date"],
  ["record_date", "Record date"],
  ["amount", "Amount / unit"],
];

export default function CashflowEvidencePanel({
  flow,
  row,
  evidence,
}: {
  flow: Cashflow;
  row?: FlowEvidence;
  evidence: CashflowEvidenceResponse;
}) {
  const attached = row?.sources ?? [];
  const ids = new Set(attached.flatMap((s) => s.notice_ids));
  const notices = evidence.notices.filter((n) => ids.has(n.id));
  const sourceIds = new Set(
    notices.flatMap((n) => n.source_record_ids.map((id) => `${n.source}:${id}`)),
  );
  const related = evidence.notices.filter(
    (n) =>
      !ids.has(n.id) &&
      n.reasons.includes("competing_versions") &&
      n.source_record_ids.some((id) => sourceIds.has(`${n.source}:${id}`)),
  );
  return (
    <div
      className="space-y-3 whitespace-normal text-xs"
      title="Dates-only comparison. Amounts and calculations remain NSDL-based; source reports are not proof of bank credit."
    >
      {evidence.partial ? (
        <p className="text-amber-400/80">
          Incomplete evidence set: automatic matching is disabled.
        </p>
      ) : !row?.checked ? (
        <p className="text-zinc-400">
          Outside the checked period: {calendarDate(evidence.date_from)} to{" "}
          {calendarDate(evidence.date_to)}.
        </p>
      ) : row.ambiguous ? (
        <p className="text-zinc-400">
          The NSDL schedule contains ambiguous same-date entries; no amount-based guess was made.
        </p>
      ) : (
        attached.length === 0 && (
          <p className="text-zinc-400">
            No exact captured notice was matched to this flow. This is not evidence of non-payment.
            Review unmatched notices below.
          </p>
        )
      )}
      {verdictFor(attached, notices).map((v) => (
        <p
          key={v.text}
          className={
            v.tone === "attention"
              ? "text-amber-400/80"
              : v.tone === "ok"
                ? "text-zinc-300"
                : "text-zinc-400"
          }
        >
          {v.text}
          {v.link && (
            <>
              {" · "}
              <a
                href={v.link}
                target="_blank"
                rel="noopener noreferrer"
                className="text-zinc-400 underline decoration-zinc-700 underline-offset-2 hover:text-zinc-100"
              >
                source ↗
              </a>
            </>
          )}
        </p>
      ))}
      <details className="text-zinc-400">
        <summary className="cursor-pointer text-[11px] hover:text-zinc-200">Details</summary>
        <div className="mt-3 space-y-3">
          {attached.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="border-b border-zinc-800 text-left text-zinc-400">
                    <th className="py-2 pr-3 font-medium">Field</th>
                    <th className="px-2 font-medium">NSDL</th>
                    {attached.map((s) => (
                      <th key={s.source} className="px-2 font-medium">
                        {s.source}
                        <div className="font-normal text-[10px]">
                          {s.anchor === "record_date" ? "Record date only" : "Due-date anchored"}
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {FIELDS.map(([field, label]) => (
                    <tr key={field} className="border-b border-zinc-800/50">
                      <td className="py-2 pr-3 text-zinc-400">{label}</td>
                      <td className="px-2 text-zinc-300">
                        {field === "amount"
                          ? `${flow.estimated ? "~" : ""}${fmt.inr(flow.amount)}${flow.estimated ? " (estimated)" : ""}`
                          : calendarDate(flow[field])}
                      </td>
                      {attached.map((s) => {
                        const comparison = s.fields.find((f) => f.field === field);
                        const dates = comparison?.exchange_dates ?? [];
                        const attention =
                          comparison?.state === "different" || comparison?.state === "conflicting";
                        return (
                          <td
                            key={s.source}
                            className={`px-2 ${attention ? "text-amber-400/80" : "text-zinc-400"}`}
                          >
                            {dates.length ? dates.map(calendarDate).join(" / ") : "Not supplied"}
                            {comparison?.state === "agree" && (
                              <span className="block text-[10px] text-zinc-400">Date agrees</span>
                            )}
                            {comparison?.state === "different" && (
                              <span className="block text-[10px]">Date differs</span>
                            )}
                            {comparison?.state === "conflicting" && (
                              <span className="block text-[10px]">Source versions differ</span>
                            )}
                            {comparison?.state === "not_supplied" && dates.length > 0 && (
                              <span className="block text-[10px] text-zinc-400">
                                NSDL date unavailable
                              </span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <SourceAvailability sources={evidence.sources} />
          {[...notices, ...related].map((notice) => (
            <NoticeCard key={notice.id} notice={notice} />
          ))}
        </div>
      </details>
    </div>
  );
}
