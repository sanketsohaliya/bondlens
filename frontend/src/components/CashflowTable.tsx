import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useId, useState } from "react";
import { api, fmt } from "../api/client";
import type { BondDetail, CashflowEvidenceResponse, FlowEvidence } from "../api/types";
import {
  buyerEligibility,
  calendarAsOf,
  calendarDate,
  evidenceMatchesSchedule,
  flowKey,
  splitCashflows,
} from "../lib/cashflowEvidence";
import CashflowEvidencePanel, { NoticeCard, SourceAvailability } from "./CashflowEvidencePanel";

type CashflowBond = Pick<
  BondDetail,
  "isin" | "cashflows" | "schedule_shape" | "amounts_estimated" | "future_total_per_unit" | "as_of"
>;

export default function CashflowTable({ bond: b }: { bond: CashflowBond }) {
  const asOf = calendarAsOf(b.as_of);
  const queryClient = useQueryClient();
  const evidenceQuery = useQuery({
    queryKey: ["cashflow-evidence", b.isin, asOf],
    queryFn: () => api.cashflowEvidence(b.isin),
    staleTime: 60_000,
    retry: 1,
  });
  const compatible = evidenceMatchesSchedule(b.isin, asOf, b.cashflows, evidenceQuery.data);
  const evidence = compatible && !evidenceQuery.isError ? evidenceQuery.data : undefined;
  const mismatch = !evidenceQuery.isError && !!evidenceQuery.data && !compatible;
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["bond", b.isin] });
    void evidenceQuery.refetch();
  };
  return (
    <CashflowSchedule
      bond={b}
      asOf={asOf}
      evidence={evidence}
      loading={evidenceQuery.isFetching}
      unavailable={!!evidenceQuery.error}
      mismatch={mismatch}
      refresh={refresh}
    />
  );
}

export function CashflowSchedule({
  bond: b,
  asOf,
  evidence,
  loading = false,
  unavailable = false,
  mismatch = false,
  refresh,
}: {
  bond: Pick<
    BondDetail,
    "isin" | "cashflows" | "schedule_shape" | "amounts_estimated" | "future_total_per_unit"
  >;
  asOf: string;
  evidence?: CashflowEvidenceResponse;
  loading?: boolean;
  unavailable?: boolean;
  mismatch?: boolean;
  refresh?: () => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const prefix = useId();
  const { upcoming, past, undated } = splitCashflows(b.cashflows, asOf);
  const byKey = new Map(evidence?.flows.map((f) => [f.key, f]) ?? []);
  const occurrences = new Map<string, number>();
  const unmatchedIds = new Set(evidence?.unmatched_notice_ids ?? []);
  const unmatched = evidence?.notices.filter((n) => unmatchedIds.has(n.id)) ?? [];
  const cutoff = new Date(`${asOf}T00:00:00Z`);
  cutoff.setUTCDate(cutoff.getUTCDate() + 90);
  const checkedTo = evidence?.date_to ?? cutoff.toISOString().slice(0, 10);
  const sections = [
    { title: "Upcoming scheduled payments", flows: upcoming, past: false },
    { title: "Past scheduled dates", flows: past, past: true },
    { title: "Unspecified schedule dates", flows: undated, past: false },
  ];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-400">
        <span>
          Schedule: <span className="text-zinc-300 capitalize">{b.schedule_shape}</span>
        </span>
        <span>{b.cashflows.length} total flows</span>
        <span>As of {calendarDate(asOf)}</span>
        {b.amounts_estimated && <span className="text-amber-500">some amounts estimated</span>}
      </div>
      <div className="text-xs text-zinc-400">
        Receivable by a new buyer under the current model:{" "}
        <span className="text-zinc-300">{fmt.inr(b.future_total_per_unit)}/unit</span>
        {" · "}
        {b.cashflows.filter((f) => f.future).length} flows
      </div>
      <div className="rounded-md border border-zinc-800 bg-zinc-900/30 px-3 py-2 space-y-1.5">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <span className="text-zinc-400">
            Exchange date checks · {calendarDate(asOf)} to {calendarDate(checkedTo)}
          </span>
          {refresh && (
            <button
              type="button"
              onClick={refresh}
              className="text-[11px] text-zinc-400 hover:text-zinc-200"
            >
              Refresh checks
            </button>
          )}
        </div>
        <p className="text-[11px] text-zinc-400">
          NSDL amounts and calculations are unchanged. Date agreement is not proof of bank receipt.
        </p>
        {loading && (
          <p role="status" className="text-[11px] text-zinc-400">
            Loading exchange evidence; the NSDL schedule remains available.
          </p>
        )}
        {unavailable && (
          <p role="status" className="text-[11px] text-zinc-400">
            Exchange evidence is unavailable. The NSDL schedule is shown unchanged.
          </p>
        )}
        {mismatch && (
          <p role="status" className="text-[11px] text-amber-400/80">
            The schedule/date snapshot changed. Refresh before comparing; stale evidence is not
            attached.
          </p>
        )}
        {evidence && (
          <>
            <SourceAvailability sources={evidence.sources} />
            <p className="text-[11px] text-zinc-400">
              {evidence.summary.matched_flows} flows with date-linked evidence ·{" "}
              {evidence.summary.differences} with date differences or conflicting versions
              {" · "}
              {evidence.summary.unmatched_notices} notices to review
            </p>
            {evidence.partial && (
              <p className="text-[11px] text-amber-400/80">
                Partial evidence set; automatic comparisons are disabled.
              </p>
            )}
          </>
        )}
        {unmatched.some((n) => n.redemption_form === "early") && (
          <p className="text-[11px] text-amber-400/80">
            An early-redemption entry is recorded below. Review its source; no maturity or yield has
            been changed.
          </p>
        )}
      </div>
      {sections
        .filter((s) => s.flows.length > 0)
        .map((section, sectionIndex) => (
          <div key={section.title}>
            <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-2">
              {section.title}
            </h3>
            {section.past && (
              <p className="mb-2 text-[11px] text-zinc-400">
                A past scheduled date does not confirm that payment was received.
              </p>
            )}
            <div className="rounded-lg border border-zinc-800 overflow-x-auto scroll-slim">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-zinc-400 bg-zinc-900/60 border-b border-zinc-800">
                    <th
                      className="px-3 py-2 font-medium"
                      title="NSDL planned payment date, falling back to contractual due date; not proof of receipt"
                    >
                      Scheduled
                    </th>
                    <th className="px-3 py-2 font-medium">Event</th>
                    <th className="px-3 py-2 font-medium">Record</th>
                    <th className="px-3 py-2 font-medium text-right">Amount / unit</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/60">
                  {section.flows.map((flow, index) => {
                    const key = flowKey(flow);
                    const row = byKey.get(key);
                    const id = `${prefix}-${sectionIndex}-${index}`;
                    const occurrence = occurrences.get(key) ?? 0;
                    occurrences.set(key, occurrence + 1);
                    const selection = `${b.isin}:${asOf}:${key}:${occurrence}`;
                    const open = expanded === selection;
                    const effective = flow.payment_date ?? flow.due_date;
                    const checkable = row?.checked || (effective >= asOf && effective <= checkedTo);
                    const eligibility = buyerEligibility(flow, asOf);
                    return (
                      <Fragment key={`${key}:${index}`}>
                        <tr className={section.past ? "text-zinc-400" : ""}>
                          <td className="px-3 py-2 whitespace-nowrap">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span
                                title={
                                  flow.payment_date && flow.payment_date !== flow.due_date
                                    ? `Contractual due date: ${calendarDate(flow.due_date)}`
                                    : undefined
                                }
                              >
                                {calendarDate(effective)}
                              </span>
                              {checkable &&
                                (row?.sources.length ? (
                                  row.sources.map((source) => (
                                    <button
                                      key={source.source}
                                      type="button"
                                      aria-expanded={open}
                                      aria-controls={id}
                                      aria-label={`Compare ${source.source} dates for ${flow.event} due ${flow.due_date}`}
                                      onClick={() => setExpanded(open ? null : selection)}
                                      className={`min-h-6 min-w-6 rounded border px-1 py-0.5 text-[10px] ${source.state === "conflicting" || source.fields.some((f) => f.state === "different" || f.state === "conflicting") ? "border-amber-500/30 text-amber-400/80" : "border-zinc-700 text-zinc-400 hover:text-zinc-200"}`}
                                    >
                                      {source.source}
                                    </button>
                                  ))
                                ) : (
                                  <button
                                    type="button"
                                    aria-expanded={open}
                                    aria-controls={id}
                                    aria-label={`Check exchange evidence for ${flow.event} due ${flow.due_date}`}
                                    onClick={() => setExpanded(open ? null : selection)}
                                    className="min-h-6 min-w-6 rounded border border-zinc-800 px-1 py-0.5 text-[10px] text-zinc-400 hover:text-zinc-200"
                                  >
                                    Check
                                  </button>
                                ))}
                            </div>
                            {(eligibility ||
                              (flow.payment_date && flow.payment_date !== flow.due_date)) && (
                              <details className="mt-0.5 text-[11px] text-zinc-400 whitespace-normal">
                                <summary className="min-h-6 cursor-pointer">
                                  {eligibility
                                    ? "New-buyer excluded"
                                    : `Contractual due ${calendarDate(flow.due_date)}`}
                                </summary>
                                <div className="max-w-64 space-y-1 py-1">
                                  {eligibility && <p>{eligibility}</p>}
                                  <p>
                                    NSDL contractual due: {calendarDate(flow.due_date)}. Planned
                                    payment: {calendarDate(effective)}.
                                  </p>
                                  <p>These are scheduled dates, not proof of bank credit.</p>
                                </div>
                              </details>
                            )}
                          </td>
                          <td className="px-3 py-2">
                            <span
                              className={`text-xs px-1.5 py-0.5 rounded capitalize ${flow.event === "redemption" ? "bg-emerald-500/10 text-emerald-400" : "bg-zinc-500/10 text-zinc-400"}`}
                            >
                              {flow.event}
                            </span>
                          </td>
                          <td
                            className={`px-3 py-2 whitespace-nowrap ${recordDiffers(row) ? "text-amber-400/80" : "text-zinc-400"}`}
                          >
                            {calendarDate(flow.record_date)}
                          </td>
                          <td
                            className="px-3 py-2 text-right whitespace-nowrap"
                            title="NSDL-derived amount; not validated by exchange date evidence"
                          >
                            {fmt.inr(flow.amount)}
                            {flow.estimated && (
                              <span
                                className="text-amber-500 ml-1"
                                title="Estimated from coupon rate"
                              >
                                ~
                              </span>
                            )}
                          </td>
                        </tr>
                        {open && (
                          <tr>
                            <td colSpan={4} id={id} className="bg-zinc-900/40 p-3">
                              {evidence ? (
                                <CashflowEvidencePanel flow={flow} row={row} evidence={evidence} />
                              ) : (
                                <p className="text-xs text-zinc-400">
                                  {loading
                                    ? "Exchange evidence is loading."
                                    : "No current evidence snapshot is available. Refresh checks to retry."}{" "}
                                  NSDL values remain unchanged.
                                </p>
                              )}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      {unmatched.length > 0 && (
        <details
          className="rounded-md border border-zinc-800 p-3"
          open={unmatched.some((n) => n.redemption_form === "early")}
        >
          <summary className="text-xs font-medium text-zinc-400 cursor-pointer">
            Unmatched / review-only exchange notices ({unmatched.length})
          </summary>
          <p className="my-2 text-[11px] text-zinc-400">
            No nearest-date guesses were made. An unmatched notice does not establish a missing or
            overdue payment.
          </p>
          <div className="space-y-2">
            {unmatched.map((notice) => (
              <NoticeCard key={notice.id} notice={notice} />
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function recordDiffers(row: FlowEvidence | undefined): boolean {
  return (
    row?.sources.some((s) =>
      s.fields.some(
        (f) => f.field === "record_date" && ["different", "conflicting"].includes(f.state),
      ),
    ) ?? false
  );
}
