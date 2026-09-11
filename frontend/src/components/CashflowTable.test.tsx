import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Cashflow, CashflowEvidenceResponse } from "../api/types";
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
const bond = {
  isin: "TEST",
  cashflows: [flow],
  schedule_shape: "plain",
  amounts_estimated: false,
  future_total_per_unit: 1234,
};

describe("cashflow schedule presentation", () => {
  it("shows upcoming holder payments separately from unchanged new-buyer totals", () => {
    const html = renderToStaticMarkup(<CashflowSchedule bond={bond} asOf="2026-09-11" />);
    expect(html).toContain("Upcoming scheduled payments");
    expect(html).not.toContain("Past scheduled dates");
    expect(html).toContain("New-buyer excluded");
    expect(html).toContain("1,234");
    expect(html).toContain("0 flows");
    expect(html).toContain("90");
  });

  it("does not hide the NSDL schedule on evidence loading or errors", () => {
    const html = renderToStaticMarkup(
      <CashflowSchedule bond={bond} asOf="2026-09-11" unavailable loading />,
    );
    expect(html).toContain("NSDL schedule is shown unchanged");
    expect(html).toContain("25 Sept 2026");
    expect(html).toContain("Check");
  });

  it("suppresses previously current evidence after a real query refetch fails", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const key = ["cashflow-evidence", "TEST", "2026-09-11"];
    const view: CashflowEvidenceResponse = {
      isin: "TEST",
      as_of: "2026-09-11",
      date_from: "2026-09-11",
      date_to: "2026-12-10",
      version: "1",
      partial: false,
      notices: [],
      unmatched_notice_ids: [],
      sources: [{ source: "NSE", status: "fresh", history_complete: true, reasons: [] }],
      flows: [
        {
          key: flowKey(flow),
          event: flow.event,
          due_date: flow.due_date,
          record_date: flow.record_date,
          payment_date: flow.payment_date ?? null,
          checked: true,
          ambiguous: false,
          sources: [],
        },
      ],
      summary: { checked_flows: 1, matched_flows: 0, differences: 0, unmatched_notices: 0 },
    };
    try {
      await client.fetchQuery({ queryKey: key, queryFn: async () => view });
      const render = () =>
        renderToStaticMarkup(
          <QueryClientProvider client={client}>
            <CashflowTable bond={{ ...bond, as_of: "2026-09-11" }} />
          </QueryClientProvider>,
        );
      expect(render()).toContain("Capture current");
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
      expect(failed).toContain("Exchange evidence is unavailable");
      expect(failed).not.toContain("Capture current");
      expect(failed).toContain("25 Sept 2026");
    } finally {
      client.clear();
    }
  });

  it("labels past dates as scheduled, not received", () => {
    const html = renderToStaticMarkup(<CashflowSchedule bond={bond} asOf="2026-10-01" />);
    expect(html).toContain("Past scheduled dates");
    expect(html).toContain("does not confirm that payment was received");
    expect(html).not.toContain("Received so far");
  });
});
