import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Cashflow, CashflowEvidenceResponse, ExchangeNotice } from "../api/types";
import { flowKey } from "../lib/cashflowEvidence";
import CashflowEvidencePanel, { NoticeCard } from "./CashflowEvidencePanel";

const flow: Cashflow = {
  event: "interest",
  due_date: "2026-09-27",
  record_date: "2026-09-12",
  payment_date: "2026-09-25",
  amount: 90,
  estimated: true,
  future: true,
};
const notice: ExchangeNotice = {
  id: "bse-1",
  source: "BSE",
  source_record_ids: [],
  raw_ids: [1],
  representation_count: 1,
  category: "Payment of Interest",
  headline: "<script>unsafe</script>",
  event_isin: "TEST",
  mentioned_isins: ["TEST"],
  events: ["interest"],
  redemption_form: null,
  nature: "Payment of Interest",
  due_date: null,
  record_date: "2026-09-12",
  ex_date: "2026-09-11",
  reported_payment_date: null,
  published_at: null,
  first_captured_at: "2026-09-11T00:00:00Z",
  last_captured_at: null,
  links: ["https://www.bseindia.com/", "javascript:alert(1)"],
  status: "matched",
  reasons: [],
  flow_keys: [flowKey(flow)],
  unmatched_events: [],
};
const view: CashflowEvidenceResponse = {
  isin: "TEST",
  as_of: "2026-09-11",
  date_from: "2026-09-11",
  date_to: "2026-12-10",
  version: "1",
  partial: false,
  sources: [],
  notices: [notice],
  unmatched_notice_ids: [],
  summary: { checked_flows: 1, matched_flows: 1, differences: 0, unmatched_notices: 0 },
  flows: [
    {
      key: flowKey(flow),
      event: flow.event,
      due_date: flow.due_date,
      record_date: flow.record_date,
      payment_date: flow.payment_date ?? null,
      checked: true,
      ambiguous: false,
      sources: [
        {
          source: "BSE",
          anchor: "record_date",
          state: "matched",
          notice_ids: [notice.id],
          fields: [
            {
              field: "due_date",
              state: "not_supplied",
              nsdl_date: flow.due_date,
              exchange_dates: [],
            },
            {
              field: "record_date",
              state: "agree",
              nsdl_date: flow.record_date,
              exchange_dates: ["2026-09-12"],
            },
            {
              field: "payment_date",
              state: "not_supplied",
              nsdl_date: flow.payment_date ?? null,
              exchange_dates: [],
            },
            { field: "amount", state: "not_supplied", nsdl_date: null, exchange_dates: [] },
          ],
        },
      ],
    },
  ],
};

describe("cashflow evidence panel", () => {
  it("labels BSE agreement as record-date-only and leaves amount unchecked", () => {
    const html = renderToStaticMarkup(
      <CashflowEvidencePanel flow={flow} row={view.flows[0]} evidence={view} />,
    );
    expect(html).toContain("Record date only");
    expect(html).toContain("Not supplied");
    expect(html).toContain("NSDL");
    expect(html).toContain("90");
    expect(html).not.toContain("cashflow confirmed");
    expect(html).not.toContain("₹0");
    expect(html).not.toContain('<p class="text-zinc-400">Dates-only comparison');
    expect(html).not.toContain("Agreement here does not resolve");
  });

  it("escapes source text and does not render unsafe external links", () => {
    const html = renderToStaticMarkup(<NoticeCard notice={notice} />);
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("javascript:");
    expect(html).toContain("First captured");
    expect(html).not.toContain("Filed on");
  });
});
