import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type {
  Cashflow,
  CashflowEvidenceResponse,
  ExchangeNotice,
  FlowSourceEvidence,
} from "../api/types";
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
  it("separates schedule metadata and independent evidence status facts", () => {
    const html = renderToStaticMarkup(
      <CashflowSchedule
        bond={bond}
        asOf="2026-09-11"
        evidence={view([], {
          partial: true,
          sources: [{ source: "NSE", status: "stale", history_complete: true, reasons: [] }],
          summary: { checked_flows: 2, matched_flows: 0, differences: 1, unmatched_notices: 0 },
        })}
        refresh={() => {}}
      />,
    );
    const text = html.replace(/<[^>]*>/g, "");
    expect(text).toContain("Schedule: plain | 1 future / 2 total flows");
    expect(text).toContain("1 date difference | partial evidence | NSE capture stale");
    expect(text).not.toContain("flows | ");
  });

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

  it("does not present an incomplete schedule subtotal as a complete future payout", () => {
    const html = renderToStaticMarkup(
      <CashflowSchedule bond={{ ...bond, future_total_per_unit: null }} asOf="2026-09-11" />,
    );
    expect(html).toContain("Future (total unavailable)");
    expect(html).not.toContain("Future (₹0");
  });

  it("marks past buyback alternatives as optional rather than received payments", () => {
    const alternative: Cashflow = {
      ...flow,
      event: "redemption",
      due_date: "2025-09-01",
      payment_date: "2025-09-01",
      record_date: null,
      future: false,
      optional: true,
    };
    const html = renderToStaticMarkup(
      <CashflowSchedule bond={{ ...bond, cashflows: [alternative] }} asOf="2026-09-11" />,
    );
    expect(html).toContain(">optional<");
    expect(html).toContain("not a receipt");
  });

  it("explains normalized combined payments without calling them estimates", () => {
    const payment: Cashflow = {
      ...receivable,
      event: "redemption",
      amount: 1000,
      adjustments: [
        {
          kind: "combined_payment_split",
          previous_amount: 1100,
          amount: 1000,
          reason: "Split published terminal payment; total unchanged",
        },
      ],
    };
    const html = renderToStaticMarkup(
      <CashflowSchedule bond={{ ...bond, cashflows: [payment] }} asOf="2026-09-11" />,
    );
    expect(html).toContain("normalized");
    expect(html).toContain("total unchanged");
    expect(html.replace(/<[^>]*>/g, "")).toContain("Before: ₹1,100 | After: ₹1,000");
    expect(html).toContain("₹1,100");
    expect(html).not.toContain("some amounts estimated");
  });

  it("identifies an added principal rather than claiming it is a coupon estimate", () => {
    const payment: Cashflow = {
      ...receivable,
      event: "redemption",
      amount: 1000,
      estimated: true,
      adjustments: [
        {
          kind: "principal_synthesized",
          previous_amount: null,
          amount: 1000,
          reason: "Added principal from face and maturity; no source redemption row",
        },
      ],
    };
    const html = renderToStaticMarkup(
      <CashflowSchedule
        bond={{ ...bond, amounts_estimated: true, cashflows: [payment] }}
        asOf="2026-09-11"
      />,
    );
    expect(html).toContain("1 future amount estimated");
    expect(html).toContain("Added principal from face and maturity");
    expect(html).toContain("not supplied");
    expect(html).not.toContain("estimated from coupon rate");
  });

  it("shows an exchange record date in the Record column, not under the amount", () => {
    const moved: Cashflow = {
      ...receivable,
      record_date: "2026-10-13",
      adjustments: [
        {
          kind: "record_date_from_exchange",
          previous_amount: 101,
          amount: 101,
          reason:
            "Record date 13 Oct 2026 per BSE notice (NSDL lists 21 Oct 2026); eligibility follows the exchange",
          source: "BSE",
          previous_record_date: "2026-10-21",
        } as never,
      ],
    };
    const html = renderToStaticMarkup(
      <CashflowSchedule bond={{ ...bond, cashflows: [moved] }} asOf="2026-09-11" />,
    );
    const text = html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
    expect(text).toContain("13 Oct 2026 BSE 21 Oct");
    expect(html).toContain("<s ");
    expect(html).not.toContain("exchange record date");
    expect(html).not.toContain("normalized");
    expect(html).toContain('title="Record date 13 Oct 2026 per BSE notice');
  });

  it("past rows carry only a dim source chip", () => {
    const paid: Cashflow = {
      ...flow,
      due_date: "2026-08-28",
      payment_date: "2026-08-28",
      record_date: "2026-08-13",
      adjustments: [
        {
          kind: "record_date_from_exchange",
          previous_amount: 101,
          amount: 101,
          reason:
            "Record date 13 Aug 2026 per BSE notice (NSDL lists 21 Aug 2026); eligibility follows the exchange",
          source: "BSE",
          previous_record_date: "2026-08-21",
        } as never,
      ],
    };
    const html = renderToStaticMarkup(
      <CashflowSchedule bond={{ ...bond, cashflows: [paid] }} asOf="2026-09-11" />,
    );
    expect(html).toContain("BSE");
    expect(html).toContain("opacity-60");
    expect(html).not.toContain("<s ");
  });

  it("distinguishes estimates outside current buyer cashflows", () => {
    const html = renderToStaticMarkup(
      <CashflowSchedule
        bond={{
          ...bond,
          amounts_estimated: true,
          cashflows: [{ ...flow, estimated: true }, receivable],
        }}
        asOf="2026-09-11"
      />,
    );
    expect(html).toContain("Only past/excluded amounts estimated");
    expect(html).not.toContain("future amount estimated");
  });

  it("dims a record-date-passed row per cell, keeps the tag legible and strikes the amount", () => {
    const html = renderToStaticMarkup(<CashflowSchedule bond={bond} asOf="2026-09-11" />);
    expect(html).toContain(">excluded<");
    expect(html).toContain("Record date has passed");
    expect(html).not.toContain("<details");
    // Row opacity would drag the tag down with it; the dim lives on the cells instead.
    expect(html).not.toContain("opacity-60");
    // The tag uses the same chip token as an agreeing BSE chip, at full strength.
    const tag = html.match(
      /<span class="([^"]*)"[^>]*title="Record date has passed[^"]*">excluded<\/span>/,
    );
    expect(tag).not.toBeNull();
    expect(tag?.[1]).toContain("text-zinc-400");
    expect(tag?.[1]).toContain("border-zinc-700");
    expect(tag?.[1]).not.toContain("text-zinc-500");
    expect(tag?.[1]).not.toContain("amber");
    // Sizing is pinned, not inherited from line-height, so the box is the same
    // whether the chip is a flex item or not.
    expect(tag?.[1]).toContain("h-[18px]");
    expect(tag?.[1]).toContain("inline-flex");
    expect(tag?.[1]).not.toContain("leading-4");
    expect(tag?.[1]).not.toContain("ml-1.5");

    // With exchange evidence the BSE chip and the tag sit on the same row and
    // must share one class string - the single guard against size drift.
    const agreeing: FlowSourceEvidence = {
      source: "BSE",
      anchor: "due_date",
      state: "matched",
      notice_ids: [],
      fields: [
        {
          field: "record_date",
          state: "agree",
          nsdl_date: "2026-09-10",
          exchange_dates: ["2026-09-10"],
        },
      ],
    };
    const withChip = renderToStaticMarkup(
      <CashflowSchedule bond={bond} asOf="2026-09-11" evidence={view([agreeing])} />,
    );
    // Attribute order differs between the two spans (title first on the chip),
    // so locate the opening tag first and read its class from anywhere inside it.
    const classOf = (label: string) =>
      withChip
        .match(new RegExp(`<span([^>]*)>${label}</span>`))?.[1]
        ?.match(/class="([^"]*)"/)?.[1];
    expect(classOf("BSE")).toBeDefined();
    expect(classOf("excluded")).toBe(classOf("BSE"));
    // The tag is a sibling of the compare button, not inside it: clicking
    // "excluded" must not open the comparison panel.
    const cell = withChip.slice(withChip.indexOf(">BSE<"), withChip.indexOf(">excluded<"));
    expect(cell).toContain("</button>");
    // Only the excluded row's amount is struck, and only its cells are muted.
    // Give the two rows distinct amounts so each cell can be attributed.
    const html2 = renderToStaticMarkup(
      <CashflowSchedule
        bond={{ ...bond, cashflows: [flow, { ...receivable, amount: 77 }] }}
        asOf="2026-09-11"
      />,
    );
    const amountCells = [
      ...html2.matchAll(/<td class="([^"]*text-right[^"]*)"[^>]*>(₹[\d,]+)/g),
    ].map((m) => [m[2], m[1]] as const);
    expect(amountCells).toHaveLength(2);
    const excludedAmount = amountCells.find(([v]) => v === "₹90")?.[1] ?? "";
    const normalAmount = amountCells.find(([v]) => v === "₹77")?.[1] ?? "";
    expect(excludedAmount).toContain("line-through");
    expect(excludedAmount).toContain("text-zinc-500");
    expect(normalAmount).not.toContain("line-through");
    expect(normalAmount).not.toContain("text-zinc-500");
  });

  it("dims an optional exit scenario without striking its amount", () => {
    const alternative: Cashflow = {
      ...flow,
      event: "redemption",
      due_date: "2027-09-01",
      payment_date: "2027-09-01",
      record_date: null,
      future: true,
      optional: true,
    };
    const html = renderToStaticMarkup(
      <CashflowSchedule
        bond={{ ...bond, cashflows: [receivable, alternative] }}
        asOf="2026-09-11"
      />,
    );
    expect(html).toContain(">optional<");
    expect(html).not.toContain("opacity-60");
    expect(html).not.toContain("line-through");
  });

  it("marks evidence rows with a source chip and shows no summary when nothing needs attention", () => {
    const agree = { ...differing, fields: [{ ...differing.fields[0], state: "agree" as const }] };
    const html = renderToStaticMarkup(
      <CashflowSchedule bond={bond} asOf="2026-09-11" evidence={view([agree])} />,
    );
    expect(html).toContain('aria-expanded="false"');
    expect(html).toMatch(/<span[^>]*border-zinc-700[^>]*>NSE<\/span>/);
    expect(html).not.toContain("rounded-full");
    expect(html).not.toContain(">Check<");
    expect(html).not.toContain("to review");
    expect(html).not.toContain("Capture current");
  });

  it("colours the chip amber and shows one attention line when a date differs", () => {
    const html = renderToStaticMarkup(
      <CashflowSchedule
        bond={bond}
        asOf="2026-09-11"
        evidence={view([differing], {
          summary: { checked_flows: 2, matched_flows: 1, differences: 1, unmatched_notices: 0 },
        })}
      />,
    );
    expect(html).toMatch(/<span[^>]*border-amber-500\/30[^>]*>NSE<\/span>/);
    expect(html).toContain("1 date difference");
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

  it("renders past dates dimmed under a plain heading, without striking received amounts", () => {
    const html = renderToStaticMarkup(<CashflowSchedule bond={bond} asOf="2026-11-01" />);
    expect(html).toContain(">Past<");
    expect(html).not.toContain("does not confirm");
    expect(html).not.toContain("opacity-60");
    // Both rows are past: both dimmed, neither lost, so no strike-through anywhere.
    expect(html).not.toContain("line-through");
    expect((html.match(/text-zinc-500/g) ?? []).length).toBeGreaterThanOrEqual(4);
  });

  const orphan: ExchangeNotice = {
    id: "o1",
    source: "BSE",
    source_record_ids: [],
    raw_ids: [7],
    representation_count: 1,
    category: "Early Redemption of NCD",
    headline: "Early Redemption of NCD",
    event_isin: "TEST",
    mentioned_isins: ["TEST"],
    events: ["redemption"],
    redemption_form: "early",
    nature: null,
    due_date: null,
    record_date: "2026-09-15",
    ex_date: "2026-09-15",
    reported_payment_date: null,
    published_at: null,
    first_captured_at: "2026-09-11T03:51:05Z",
    last_captured_at: null,
    links: ["https://www.bseindia.com/early"],
    status: "review_only",
    reasons: ["early_redemption_review"],
    flow_keys: [],
    unmatched_events: ["redemption"],
  };
  const withOrphan = () =>
    view([], {
      notices: [orphan],
      unmatched_notice_ids: ["o1"],
      summary: { checked_flows: 2, matched_flows: 0, differences: 0, unmatched_notices: 1 },
    });

  it("gives residual exchange notices a table section between Future and Past", () => {
    const past = {
      ...flow,
      due_date: "2026-08-27",
      payment_date: "2026-08-27",
      record_date: "2026-08-12",
    };
    const html = renderToStaticMarkup(
      <CashflowSchedule
        bond={{ ...bond, cashflows: [...bond.cashflows, past] }}
        asOf="2026-09-11"
        evidence={withOrphan()}
      />,
    );
    expect(html).toContain("Exchange notices not in NSDL schedule (1)");
    const future = html.indexOf("Future (");
    const notices = html.indexOf("Exchange notices not in NSDL schedule");
    const pastIdx = html.indexOf(">Past<");
    expect(future).toBeLessThan(notices);
    expect(notices).toBeLessThan(pastIdx);
    expect(html).toContain(">Announced<");
    expect(html).toContain("15 Sept 2026");
    expect(html).toContain("Early Redemption of NCD");
    expect(html).toContain('href="https://www.bseindia.com/early"');
    expect(html).not.toContain("to review");
    expect(html).not.toContain("<details");
  });

  it("hides the residual section when every notice is matched", () => {
    const html = renderToStaticMarkup(
      <CashflowSchedule bond={bond} asOf="2026-09-11" evidence={view([differing])} />,
    );
    expect(html).not.toContain("Exchange notices not in NSDL schedule");
    expect(html).not.toContain(">Announced<");
  });

  it("calls out an early redemption under the Future heading", () => {
    const html = renderToStaticMarkup(
      <CashflowSchedule bond={bond} asOf="2026-09-11" evidence={withOrphan()} />,
    );
    const heading = html.indexOf("Future (");
    const callout = html.indexOf("reports an early redemption");
    const table = html.indexOf(">Pays<");
    expect(heading).toBeLessThan(callout);
    expect(callout).toBeLessThan(table);
    expect(html).toContain("BSE reports an early redemption with ex-date 15 Sept 2026");
    expect(html).toContain("assume the original maturity");
    const plain = renderToStaticMarkup(
      <CashflowSchedule bond={bond} asOf="2026-09-11" evidence={view([differing])} />,
    );
    expect(plain).not.toContain("early redemption");
  });
});
