import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Cashflow, CashflowEvidenceResponse, FlowSourceEvidence } from "../api/types";
import { flowKey } from "../lib/cashflowEvidence";
import CashflowTable, { CashflowSchedule } from "./CashflowTable";

const flow: Cashflow = {
  event: "interest",
  due_date: "2026-09-27",
  record_date: "2026-09-10",
  payment_date: "2026-09-25",
  amount: 90,
  estimated: false,
  future: false,
};
const receivable: Cashflow = {
  ...flow,
  due_date: "2026-10-27",
  record_date: "2026-10-12",
  payment_date: "2026-10-27",
  future: true,
};
const bond = {
  isin: "TEST",
  cashflows: [flow, receivable],
  schedule_shape: "plain",
  amounts_estimated: false,
  future_total_per_unit: 1234,
};

function view(
  sources: FlowSourceEvidence[] = [],
  extra: Partial<CashflowEvidenceResponse> = {},
): CashflowEvidenceResponse {
  return {
    isin: "TEST",
    as_of: "2026-09-11",
    date_from: "2026-09-11",
    date_to: "2026-12-10",
    version: "1",
    partial: false,
    notices: [],
    unmatched_notice_ids: [],
    sources: [{ source: "NSE", status: "fresh", history_complete: true, reasons: [] }],
    flows: bond.cashflows.map((f, i) => ({
      key: flowKey(f),
      event: f.event,
      due_date: f.due_date,
      record_date: f.record_date,
      payment_date: f.payment_date ?? null,
      checked: true,
      ambiguous: false,
      sources: i === 0 ? sources : [],
    })),
    summary: {
      checked_flows: 2,
      matched_flows: sources.length ? 1 : 0,
      differences: 0,
      unmatched_notices: 0,
    },
    ...extra,
  };
}

const differing: FlowSourceEvidence = {
  source: "NSE",
  anchor: "due_date",
  state: "matched",
  notice_ids: [],
  fields: [
    {
      field: "record_date",
      state: "different",
      nsdl_date: "2026-09-10",
      exchange_dates: ["2026-09-11"],
    },
  ],
};

describe("cashflow schedule presentation", () => {
  it("restores the simple future/past layout with the buyer total in the heading", () => {
    const html = renderToStaticMarkup(<CashflowSchedule bond={bond} asOf="2026-09-11" />);
    expect(html).toContain("Future (₹1,234/unit)");
    expect(html).toContain("1 future / 2 total flows");
    expect(html).toContain(">Pays<");
    expect(html).not.toContain("Receivable by a new buyer");
    expect(html).not.toContain("New-buyer excluded");
    expect(html).not.toContain("Exchange date checks");
    expect(html).not.toContain("not proof");
    expect(html).not.toContain("Past");
  });

  it("dims and tags a record-date-passed row without inline prose", () => {
    const html = renderToStaticMarkup(<CashflowSchedule bond={bond} asOf="2026-09-11" />);
    expect(html).toContain(">excluded<");
    expect(html).toContain("Record date has passed");
    expect(html).not.toContain("<details");
    expect(html.match(/opacity-60/g)).toHaveLength(1);
  });

  it("marks evidence rows with a dot only and shows no summary when nothing needs attention", () => {
    const agree = { ...differing, fields: [{ ...differing.fields[0], state: "agree" as const }] };
    const html = renderToStaticMarkup(
      <CashflowSchedule bond={bond} asOf="2026-09-11" evidence={view([agree])} />,
    );
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain(">Check<");
    expect(html).not.toContain(">NSE<");
    expect(html).not.toContain("to review");
    expect(html).not.toContain("Capture current");
  });

  it("shows one attention line when a date differs", () => {
    const html = renderToStaticMarkup(
      <CashflowSchedule
        bond={bond}
        asOf="2026-09-11"
        evidence={view([differing], {
          summary: { checked_flows: 2, matched_flows: 1, differences: 1, unmatched_notices: 0 },
        })}
      />,
    );
    expect(html).toContain("1 date difference");
    expect(html).toContain("text-amber-400/80");
  });

  it("collapses loading and unavailable states into a single quiet line", () => {
    const html = renderToStaticMarkup(
      <CashflowSchedule bond={bond} asOf="2026-09-11" unavailable />,
    );
    expect(html).toContain("Exchange checks unavailable");
    expect(html).toContain("25 Sept 2026");
  });

  it("suppresses previously current evidence after a real query refetch fails", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const key = ["cashflow-evidence", "TEST", "2026-09-11"];
    try {
      await client.fetchQuery({
        queryKey: key,
        queryFn: async () =>
          view([differing], {
            summary: { checked_flows: 2, matched_flows: 1, differences: 1, unmatched_notices: 0 },
          }),
      });
      const render = () =>
        renderToStaticMarkup(
          <QueryClientProvider client={client}>
            <CashflowTable bond={{ ...bond, as_of: "2026-09-11" }} />
          </QueryClientProvider>,
        );
      expect(render()).toContain("1 date difference");
      await expect(
        client.fetchQuery({
          queryKey: key,
          retry: false,
          queryFn: async () => {
            throw new Error("refresh failed");
          },
        }),
      ).rejects.toThrow("refresh failed");
      const failed = render();
      expect(failed).toContain("Exchange checks unavailable");
      expect(failed).not.toContain("1 date difference");
      expect(failed).toContain("25 Sept 2026");
    } finally {
      client.clear();
    }
  });

  it("renders past dates dimmed under a plain heading", () => {
    const html = renderToStaticMarkup(<CashflowSchedule bond={bond} asOf="2026-11-01" />);
    expect(html).toContain(">Past<");
    expect(html).not.toContain("does not confirm");
    expect(html.match(/opacity-60/g)).toHaveLength(2);
  });
});
