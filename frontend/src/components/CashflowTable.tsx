import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, type ReactNode, useId, useState } from "react";
import { api, fmt } from "../api/client";
import type {
  BondDetail,
  Cashflow,
  CashflowEvidenceResponse,
  ExchangeNotice,
  FlowEvidence,
} from "../api/types";
import {
  calendarAsOf,
  calendarDate,
  evidenceMatchesSchedule,
  excludedReason,
  flowKey,
  noticeAnnouncedDate,
  noticeFacts,
  sourceChip,
  splitCashflows,
  validLink,
} from "../lib/cashflowEvidence";
import CashflowEvidencePanel, { NoticeCard } from "./CashflowEvidencePanel";

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

const CAVEAT = "Scheduled dates from NSDL, not confirmation of receipt.";

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
  const receivable = b.cashflows.filter((f) => f.future).length;
  const summary = summaryLine(evidence, { loading, unavailable, mismatch });

  const renderRow = (flow: Cashflow, index: number, sectionIndex: number, dimSection: boolean) => {
    const key = flowKey(flow);
    const row = byKey.get(key);
    const id = `${prefix}-${sectionIndex}-${index}`;
    const occurrence = occurrences.get(key) ?? 0;
    occurrences.set(key, occurrence + 1);
    const selection = `${b.isin}:${asOf}:${key}:${occurrence}`;
    const open = expanded === selection;
    const effective = flow.payment_date ?? flow.due_date;
    const excluded = dimSection ? null : excludedReason(flow, asOf);
    const chip = sourceChip(row);
    const dateTitle =
      flow.payment_date && flow.payment_date !== flow.due_date
        ? `Contractual due date: ${calendarDate(flow.due_date)}`
        : undefined;
    return (
      <Fragment key={`${key}:${index}`}>
        <tr className={dimSection || excluded ? "opacity-60" : ""}>
          <td className="px-3 py-2 whitespace-nowrap">
            {!chip ? (
              <span title={dateTitle}>{calendarDate(effective)}</span>
            ) : (
              <button
                type="button"
                aria-expanded={open}
                aria-controls={id}
                aria-label={`${open ? "Hide" : "Show"} exchange date comparison for ${flow.event} due ${flow.due_date}`}
                onClick={() => setExpanded(open ? null : selection)}
                className="inline-flex min-h-6 items-center gap-1.5 hover:text-zinc-100"
              >
                <span title={dateTitle}>{calendarDate(effective)}</span>
                <span
                  title={
                    chip.attention
                      ? "Exchange notice differs on a date — click to compare"
                      : "Exchange notice agrees on dates — click to compare"
                  }
                  className={`rounded border px-1 text-[10px] leading-4 ${chip.attention ? "border-amber-500/30 text-amber-400/80" : "border-zinc-700 text-zinc-400"}`}
                >
                  {chip.text}
                </span>
              </button>
            )}
            {excluded && (
              <span
                className="ml-1.5 rounded border border-zinc-800 px-1 text-[10px] text-zinc-500"
                title={excluded}
              >
                excluded
              </span>
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
            className={`px-3 py-2 whitespace-nowrap ${recordDiffers(row) ? "text-amber-400/80" : "text-zinc-500"}`}
            title={
              recordDiffers(row) ? "An exchange notice states a different record date" : undefined
            }
          >
            {calendarDate(flow.record_date)}
          </td>
          <td className="px-3 py-2 text-right whitespace-nowrap">
            {fmt.inr(flow.amount)}
            {flow.estimated && (
              <span className="text-amber-500 ml-1" title="estimated from coupon rate">
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
                <p className="text-xs text-zinc-400">No current evidence snapshot is available.</p>
              )}
            </td>
          </tr>
        )}
      </Fragment>
    );
  };

  const early = unmatched.filter((n) => n.redemption_form === "early");

  const renderNoticeRow = (n: ExchangeNotice, index: number) => {
    const id = `${prefix}-notice-${index}`;
    const selection = `${b.isin}:${asOf}:notice:${n.id}`;
    const open = expanded === selection;
    const facts = noticeFacts(n);
    const announced = noticeAnnouncedDate(n);
    const link = n.links.find(validLink);
    const isEarly = n.redemption_form === "early";
    return (
      <Fragment key={n.id}>
        <tr>
          <td className="px-3 py-2 whitespace-nowrap">
            <button
              type="button"
              aria-expanded={open}
              aria-controls={id}
              aria-label={`${open ? "Hide" : "Show"} ${n.source} notice details`}
              onClick={() => setExpanded(open ? null : selection)}
              className="inline-flex min-h-6 items-center gap-1.5 hover:text-zinc-100"
            >
              <span>{calendarDate(announced)}</span>
              <span
                className={`rounded border px-1 text-[10px] leading-4 ${isEarly ? "border-amber-500/30 text-amber-400/80" : "border-zinc-700 text-zinc-400"}`}
              >
                {n.source}
              </span>
            </button>
          </td>
          <td className="px-3 py-2">
            <span
              className={`text-xs px-1.5 py-0.5 rounded ${isEarly ? "bg-amber-500/10 text-amber-400/90" : "bg-zinc-500/10 text-zinc-400"}`}
            >
              {facts.header}
            </span>
          </td>
          <td className="px-3 py-2 whitespace-nowrap text-zinc-500">
            {calendarDate(n.record_date)}
          </td>
          <td className="px-3 py-2 text-right whitespace-nowrap">
            {link ? (
              <a
                href={link}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-zinc-400 underline decoration-zinc-700 underline-offset-2 hover:text-zinc-100"
              >
                source ↗
              </a>
            ) : (
              <span className="text-zinc-600">–</span>
            )}
          </td>
        </tr>
        {open && (
          <tr>
            <td colSpan={4} id={id} className="bg-zinc-900/40 p-3">
              <NoticeCard notice={n} showLink={false} />
            </td>
          </tr>
        )}
      </Fragment>
    );
  };

  const table = (head: string, headTip: string, body: ReactNode) => (
    <div className="rounded-lg border border-zinc-800 overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-zinc-500 bg-zinc-900/60 border-b border-zinc-800">
            <th className="px-3 py-2 font-medium" title={headTip}>
              {head}
            </th>
            <th className="px-3 py-2 font-medium">Event</th>
            <th className="px-3 py-2 font-medium">Record</th>
            <th className="px-3 py-2 font-medium text-right">Amount / unit</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800/60">{body}</tbody>
      </table>
    </div>
  );

  const flowSection = (
    title: string,
    tip: string,
    flows: Cashflow[],
    dim: boolean,
    sectionIndex: number,
    callout?: ReactNode,
  ) =>
    flows.length > 0 && (
      <div key={title}>
        <h3
          className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2"
          title={tip}
        >
          {title}
        </h3>
        {callout}
        {table(
          "Pays",
          "When the money lands (payment date; scheduled due date when none published)",
          flows.map((flow, i) => renderRow(flow, i, sectionIndex, dim)),
        )}
      </div>
    );

  const earlyCallout = early.length > 0 && (
    <p className="mb-2 text-[11px] text-amber-400/80">
      {early.map((n, i) => {
        const link = n.links.find(validLink);
        const when = noticeAnnouncedDate(n);
        return (
          <span key={n.id}>
            {i > 0 && " · "}
            {n.source} reports an early redemption
            {when && ` with ${n.ex_date ? "ex-date" : "record date"} ${calendarDate(when)}`}
            {link && (
              <>
                {" · "}
                <a
                  href={link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline decoration-amber-500/40 underline-offset-2 hover:text-amber-300"
                >
                  source ↗
                </a>
              </>
            )}
          </span>
        );
      })}
      {" — schedule and yields below assume the original maturity."}
    </p>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 text-xs text-zinc-500">
        <span>
          Schedule: <span className="text-zinc-300 capitalize">{b.schedule_shape}</span>
        </span>
        <span title="Flows a buyer today would receive / all scheduled flows">
          {receivable} future / {b.cashflows.length} total flows
        </span>
        {b.amounts_estimated && <span className="text-amber-500">some amounts estimated</span>}
        {refresh && (
          <button
            type="button"
            onClick={refresh}
            aria-label="Refresh exchange checks"
            title="Refresh exchange checks"
            className="ml-auto flex h-6 w-6 items-center justify-center rounded text-zinc-500 hover:text-zinc-200"
          >
            ⟳
          </button>
        )}
      </div>

      {flowSection(
        `Future (${fmt.inr(b.future_total_per_unit)}/unit)`,
        "Total a buyer today would receive. Dimmed rows are excluded (record date passed).",
        upcoming,
        false,
        0,
        earlyCallout,
      )}

      {unmatched.length > 0 && (
        <div>
          <h3
            className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2"
            title="Captured exchange notices that could not be tied to any NSDL cashflow. Not evidence of a missing or extra payment on its own."
          >
            Exchange notices not in NSDL schedule ({unmatched.length})
          </h3>
          {table(
            "Announced",
            "The exchange's ex-date, else record date, else due date, else filing date",
            unmatched.map(renderNoticeRow),
          )}
        </div>
      )}

      {flowSection("Past", CAVEAT, past, true, 1)}
      {flowSection("Unspecified dates", CAVEAT, undated, true, 2)}

      {summary && (
        <p role="status" className={`inline-flex items-center gap-1.5 text-[11px] ${summary.tone}`}>
          <span aria-hidden="true" className="inline-block h-1.5 w-1.5 rounded-full bg-current" />
          {summary.text}
        </p>
      )}
    </div>
  );
}

function summaryLine(
  evidence: CashflowEvidenceResponse | undefined,
  state: { loading: boolean; unavailable: boolean; mismatch: boolean },
): { text: string; tone: string } | null {
  if (state.mismatch)
    return { text: "Schedule changed — refresh exchange checks", tone: "text-amber-400/80" };
  if (state.unavailable && !evidence)
    return { text: "Exchange checks unavailable", tone: "text-zinc-500" };
  if (!evidence)
    return state.loading ? { text: "Exchange checks loading…", tone: "text-zinc-500" } : null;
  const parts: string[] = [];
  const { differences } = evidence.summary;
  if (differences > 0) parts.push(`${differences} date difference${differences === 1 ? "" : "s"}`);
  if (evidence.partial) parts.push("partial evidence");
  const notFresh = evidence.sources.filter((s) => s.status !== "fresh");
  for (const s of notFresh) parts.push(`${s.source} capture ${s.status.replace("_", " ")}`);
  if (parts.length === 0) return null;
  const tone = differences > 0 || evidence.partial ? "text-amber-400/80" : "text-zinc-500";
  return { text: parts.join(" · "), tone };
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
