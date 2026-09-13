import type {
  Cashflow,
  CashflowEvidenceResponse,
  ExchangeNotice,
  FlowEvidence,
} from "../api/types";
import { calendarDate, noticeFacts, validLink, verdictFor } from "../lib/cashflowEvidence";
import InlineMetadata, { MetadataSeparator } from "./InlineMetadata";

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

const NOTICE_STATES: Record<ExchangeNotice["status"], string> = {
  matched: "Matched notice",
  unmatched: "Not matched to NSDL",
  ambiguous: "Ambiguous match",
  review_only: "Review source",
  context_only: "Related mention only",
};

export function NoticeCard({
  notice: n,
  showLink = true,
}: {
  notice: ExchangeNotice;
  showLink?: boolean;
}) {
  const facts = noticeFacts(n);
  const links = showLink ? n.links.filter(validLink) : [];
  return (
    <div className="space-y-1 text-xs whitespace-normal">
      <div className="flex flex-wrap items-baseline gap-y-0.5">
        <span className="inline-flex flex-wrap items-baseline gap-x-2">
          <span className="font-semibold text-zinc-300">{n.source}</span>
          <span className={n.redemption_form === "early" ? "text-amber-400/80" : "text-zinc-400"}>
            {facts.header}
          </span>
        </span>
        {(n.status !== "matched" || n.corroborated_by) && (
          <span className="inline-flex flex-wrap items-baseline text-[10px] text-zinc-500">
            <MetadataSeparator />
            <InlineMetadata
              parts={[
                n.status !== "matched" && NOTICE_STATES[n.status],
                n.corroborated_by && (
                  <span
                    key="corroboration"
                    title="BSE supplies only a record date. This notice was tied to the row because an NSE notice on the same row states the same record date."
                  >
                    attached via {n.corroborated_by} record date
                  </span>
                ),
              ]}
            />
          </span>
        )}
      </div>
      {facts.quote && (
        <p className="italic text-zinc-400 whitespace-pre-wrap break-words">“{facts.quote}”</p>
      )}
      {facts.dates.length > 0 && (
        <p className="text-[11px]">
          {facts.dates.map((d, i) => (
            <span key={d.label} title={d.title}>
              {i > 0 && <MetadataSeparator />}
              <span className="text-zinc-500">{d.label} </span>
              <span className="text-zinc-300">{d.value}</span>
            </span>
          ))}
        </p>
      )}
      {facts.time && <p className="text-[11px] text-zinc-500">{facts.time}</p>}
      {n.reasons.length > 0 && (
        <ul className="space-y-0.5 text-[11px] text-zinc-400">
          {n.reasons.map((reason) => (
            <li key={reason}>{reasonText(reason)}</li>
          ))}
        </ul>
      )}
      {n.unmatched_events.length > 0 && (
        <p className="text-[11px] text-zinc-400">
          Unmatched components: {n.unmatched_events.join(", ")}
        </p>
      )}
      {showLink && (
        <div className="flex flex-wrap gap-y-1 text-[11px]">
          <InlineMetadata
            parts={links.map((url, i) => (
              <a
                key={url}
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-zinc-400 underline decoration-zinc-700 underline-offset-2 hover:text-zinc-100"
              >
                source ↗{links.length > 1 ? ` ${i + 1}` : ""}
              </a>
            ))}
          />
          {links.length === 0 && (
            <span className="text-[11px] text-zinc-500">Source link not captured</span>
          )}
        </div>
      )}
    </div>
  );
}

export default function CashflowEvidencePanel({
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
  const shown = [...notices, ...related];
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
          {v.links.map((l) => (
            <span key={l.href}>
              <MetadataSeparator />
              <a
                href={l.href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-zinc-400 underline decoration-zinc-700 underline-offset-2 hover:text-zinc-100"
              >
                {l.label} ↗
              </a>
            </span>
          ))}
        </p>
      ))}
      {shown.length > 0 && (
        <details className="text-zinc-400">
          <summary className="cursor-pointer text-[11px] hover:text-zinc-200">Details</summary>
          <div className="mt-3 space-y-3">
            {shown.map((notice) => (
              <NoticeCard key={notice.id} notice={notice} showLink={false} />
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
