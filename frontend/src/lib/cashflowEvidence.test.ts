import { describe, expect, it } from "vitest";
import type { Cashflow, CashflowEvidenceResponse } from "../api/types";
import {
  calendarAsOf,
  calendarDate,
  evidenceMatchesSchedule,
  flowKey,
  splitCashflows,
} from "./cashflowEvidence";

const flow: Cashflow = {
  event: "interest",
  due_date: "2026-09-27",
  payment_date: "2026-09-25",
  record_date: "2026-09-10",
  amount: 90,
  estimated: false,
  future: false,
};

function evidence(): CashflowEvidenceResponse {
  return {
    isin: "TEST",
    as_of: "2026-09-11",
    date_from: "2026-09-11",
    date_to: "2026-12-10",
    version: "1",
    partial: false,
    sources: [],
    notices: [],
    unmatched_notice_ids: [],
    summary: { checked_flows: 1, matched_flows: 0, differences: 0, unmatched_notices: 0 },
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
  };
}

describe("cashflow calendar and evidence", () => {
  it("keeps an upcoming payment after its record date, regardless of buyer eligibility", () => {
    const rows = [flow];
    const result = splitCashflows(rows, "2026-09-11");
    expect(result.upcoming).toEqual([flow]);
    expect(result.past).toEqual([]);
    expect(rows[0].future).toBe(false);
  });

  it("groups scheduled today as upcoming and uses payment date rather than due date", () => {
    const today = { ...flow, payment_date: "2026-09-11" };
    const early = { ...flow, payment_date: "2026-09-10" };
    expect(splitCashflows([early, today], "2026-09-11")).toEqual({
      upcoming: [today],
      past: [early],
      undated: [],
    });
  });

  it("does not present sentinel dates as real future bank payments", () => {
    const sentinel = { ...flow, due_date: "9999-12-31", payment_date: null };
    expect(splitCashflows([sentinel], "2026-09-11").undated).toEqual([sentinel]);
  });

  it("uses the server date and a deterministic IST fallback", () => {
    expect(calendarAsOf("2026-09-11")).toBe("2026-09-11");
    expect(calendarAsOf(null, new Date("2026-09-10T20:00:00Z"))).toBe("2026-09-11");
    expect(calendarDate("2026-09-11")).toContain("11");
    expect(calendarDate("2026-02-31")).toBe("–");
  });

  it("refuses evidence for another bond, day or changed schedule", () => {
    expect(evidenceMatchesSchedule("TEST", "2026-09-11", [flow], evidence())).toBe(true);
    expect(evidenceMatchesSchedule("OTHER", "2026-09-11", [flow], evidence())).toBe(false);
    expect(evidenceMatchesSchedule("TEST", "2026-09-12", [flow], evidence())).toBe(false);
    expect(
      evidenceMatchesSchedule(
        "TEST",
        "2026-09-11",
        [{ ...flow, record_date: "2026-09-12" }],
        evidence(),
      ),
    ).toBe(true); // a moved record date is the exchange overlay, not a changed schedule
    expect(
      evidenceMatchesSchedule(
        "TEST",
        "2026-09-11",
        [{ ...flow, due_date: "2026-09-29" }],
        evidence(),
      ),
    ).toBe(false);
  });

  it("rejects stale evidence when duplicate date identities appear or disappear", () => {
    const duplicate = { ...flow, amount: 20 };
    expect(evidenceMatchesSchedule("TEST", "2026-09-11", [flow, duplicate], evidence())).toBe(
      false,
    );
    const cached = evidence();
    cached.flows[0].ambiguous = true;
    expect(evidenceMatchesSchedule("TEST", "2026-09-11", [flow], cached)).toBe(false);
    expect(evidenceMatchesSchedule("TEST", "2026-09-11", [flow, duplicate], cached)).toBe(true);
  });

  it("explains excluded rows briefly and grades evidence attention", async () => {
    const { excludedReason, rowAttention } = await import("./cashflowEvidence");
    expect(excludedReason(flow, "2026-09-11")).toContain("Record date has passed");
    expect(excludedReason({ ...flow, future: true }, "2026-09-11")).toBeNull();
    expect(excludedReason(flow, "2026-10-01")).toBeNull();
    const row = evidence().flows[0];
    expect(rowAttention(undefined)).toBe("none");
    expect(rowAttention(row)).toBe("none");
    const agree = {
      ...row,
      sources: [
        {
          source: "NSE" as const,
          anchor: "due_date" as const,
          state: "matched" as const,
          notice_ids: [],
          fields: [
            {
              field: "record_date" as const,
              state: "agree" as const,
              nsdl_date: null,
              exchange_dates: [],
            },
          ],
        },
      ],
    };
    expect(rowAttention(agree)).toBe("info");
    const differs = {
      ...agree,
      sources: [
        {
          ...agree.sources[0],
          fields: [{ ...agree.sources[0].fields[0], state: "different" as const }],
        },
      ],
    };
    expect(rowAttention(differs)).toBe("attention");
    expect(
      rowAttention({ ...agree, sources: [{ ...agree.sources[0], state: "conflicting" as const }] }),
    ).toBe("attention");
  });

  it("names sources on the row chip and grades its tone", async () => {
    const { sourceChip } = await import("./cashflowEvidence");
    const row = evidence().flows[0];
    const src = (source: "NSE" | "BSE", state: "agree" | "different") => ({
      source,
      anchor: "due_date" as const,
      state: "matched" as const,
      notice_ids: [],
      fields: [{ field: "record_date" as const, state, nsdl_date: null, exchange_dates: [] }],
    });
    expect(sourceChip(row)).toBeNull();
    expect(sourceChip({ ...row, sources: [src("BSE", "agree")] })).toEqual({
      text: "BSE",
      attention: false,
    });
    expect(sourceChip({ ...row, sources: [src("BSE", "agree"), src("NSE", "agree")] })).toEqual({
      text: "NSE·BSE",
      attention: false,
    });
    expect(sourceChip({ ...row, sources: [src("NSE", "different")] })).toEqual({
      text: "NSE",
      attention: true,
    });
  });

  it("reduces a notice to what the exchange actually said", async () => {
    const { noticeFacts } = await import("./cashflowEvidence");
    const base = {
      id: "b1",
      source: "BSE" as const,
      source_record_ids: [],
      raw_ids: [543],
      representation_count: 1,
      category: "Payment of Interest",
      headline: "Payment of Interest",
      event_isin: "X",
      mentioned_isins: [],
      events: ["interest" as const],
      redemption_form: null,
      nature: null,
      due_date: null,
      record_date: "2026-09-14",
      ex_date: "2026-09-11",
      reported_payment_date: null,
      published_at: null,
      first_captured_at: "2026-09-11T03:51:05Z",
      last_captured_at: null,
      links: [],
      status: "matched" as const,
      reasons: [],
      flow_keys: [],
      unmatched_events: [],
    };
    const bse = noticeFacts(base);
    expect(bse.header).toBe("Payment of Interest");
    expect(bse.quote).toBeNull();
    expect(bse.dates.map((d) => `${d.label} ${d.value}`)).toEqual([
      "Ex-date 11 Sept 2026",
      "Record date 14 Sept 2026",
    ]);
    expect(bse.dates[0].title).toContain("not a payment date");
    expect(bse.time).toBe("Captured 11 Sept 2026, 09:21 IST");

    const nse = noticeFacts({
      ...base,
      source: "NSE",
      category: "Record Date Updates",
      headline:
        "Issuer has informed the Exchange regarding Record Date Updates. Record date - 11-Sep-2026 , Due date - 27-Sep-2026",
      due_date: "2026-09-27",
      record_date: "2026-09-11",
      ex_date: null,
      reported_payment_date: "2026-09-27",
      published_at: "2026-09-10T10:00:00Z",
    });
    expect(nse.quote).toMatch(/^Issuer has informed/);
    expect(nse.dates.map((d) => d.label)).toEqual(["Record date", "Due", "Payment"]);
    expect(nse.time).toBe("Filed 10 Sept 2026, 15:30 IST");

    expect(noticeFacts({ ...base, headline: "  payment of interest  " }).quote).toBeNull();
    expect(noticeFacts({ ...base, headline: "" }).quote).toBeNull();
    const bare = noticeFacts({
      ...base,
      record_date: null,
      ex_date: null,
      first_captured_at: null,
    });
    expect(bare.dates).toEqual([]);
    expect(bare.time).toBeNull();
    expect(noticeFacts({ ...base, category: "", nature: "Interest" }).header).toBe("Interest");
  });

  it("picks the exchange's announced date in a stable order", async () => {
    const { noticeAnnouncedDate } = await import("./cashflowEvidence");
    const n = {
      ex_date: "2026-09-16",
      record_date: "2026-09-17",
      due_date: "2026-09-30",
      published_at: "2026-09-01T10:00:00Z",
    } as Parameters<typeof noticeAnnouncedDate>[0];
    expect(noticeAnnouncedDate(n)).toBe("2026-09-16");
    expect(noticeAnnouncedDate({ ...n, ex_date: null })).toBe("2026-09-17");
    expect(noticeAnnouncedDate({ ...n, ex_date: null, record_date: null })).toBe("2026-09-30");
    expect(noticeAnnouncedDate({ ...n, ex_date: null, record_date: null, due_date: null })).toBe(
      "2026-09-01",
    );
    expect(
      noticeAnnouncedDate({
        ...n,
        ex_date: null,
        record_date: null,
        due_date: null,
        published_at: null,
      }),
    ).toBeNull();
  });

  it("writes one-line verdicts that name dates and never amounts", async () => {
    const { verdictFor } = await import("./cashflowEvidence");
    const f = (
      field: "due_date" | "record_date" | "payment_date" | "amount",
      state: "agree" | "different" | "not_supplied" | "conflicting",
      nsdl: string | null,
      ex: string[] = [],
    ) => ({ field, state, nsdl_date: nsdl, exchange_dates: ex });
    const notice = (id: string, links: string[]) => ({
      id,
      source: "BSE" as const,
      source_record_ids: [],
      raw_ids: [],
      representation_count: 1,
      category: "",
      headline: "",
      event_isin: null,
      mentioned_isins: [],
      events: [],
      redemption_form: null,
      nature: null,
      due_date: null,
      record_date: null,
      ex_date: null,
      reported_payment_date: null,
      published_at: null,
      first_captured_at: null,
      last_captured_at: null,
      links,
      status: "matched" as const,
      reasons: [],
      flow_keys: [],
      unmatched_events: [],
    });
    const base = { anchor: "record_date" as const, state: "matched" as const, notice_ids: ["n1"] };

    const agree = verdictFor(
      [
        {
          ...base,
          source: "BSE",
          fields: [
            f("due_date", "not_supplied", "2026-09-27"),
            f("record_date", "agree", "2026-09-14", ["2026-09-14"]),
            f("amount", "not_supplied", null),
          ],
        },
      ],
      [notice("n1", ["javascript:alert(1)", "https://www.bseindia.com/x"])],
    );
    expect(agree).toHaveLength(1);
    expect(agree[0].text).toBe("BSE confirms record date 14 Sept 2026");
    expect(agree[0].tone).toBe("ok");
    expect(agree[0].links).toEqual([{ label: "source", href: "https://www.bseindia.com/x" }]);
    expect(agree[0].text).not.toMatch(/₹|amount/i);

    const both = verdictFor(
      [
        {
          ...base,
          source: "NSE",
          anchor: "due_date",
          fields: [
            f("due_date", "agree", "2026-09-27", ["2026-09-27"]),
            f("record_date", "agree", "2026-09-12", ["2026-09-12"]),
          ],
        },
      ],
      [],
    );
    expect(both[0].text).toBe("NSE confirms due 27 Sept 2026 and record date 12 Sept 2026");
    expect(both[0].links).toEqual([]);

    const differs = verdictFor(
      [
        {
          ...base,
          source: "NSE",
          anchor: "due_date",
          fields: [
            f("due_date", "agree", "2026-09-27", ["2026-09-27"]),
            f("record_date", "different", "2026-09-12", ["2026-09-11"]),
          ],
        },
      ],
      [],
    );
    expect(differs[0].text).toBe("NSE says record date 11 Sept 2026 — NSDL has 12 Sept 2026");
    expect(differs[0].tone).toBe("attention");

    const conflict = verdictFor(
      [
        {
          ...base,
          source: "NSE",
          state: "conflicting",
          fields: [f("record_date", "conflicting", "2026-09-12", ["2026-09-11", "2026-09-12"])],
        },
      ],
      [],
    );
    expect(conflict[0].text).toBe(
      "NSE filings disagree with each other on record date (11 Sept 2026 / 12 Sept 2026) — no version treated as authoritative",
    );
    expect(conflict[0].tone).toBe("attention");

    const empty = verdictFor(
      [{ ...base, source: "BSE", fields: [f("record_date", "not_supplied", "2026-09-12")] }],
      [],
    );
    expect(empty[0].text).toBe("BSE notice matched, but supplied no comparable dates");
    expect(empty[0].tone).toBe("muted");

    // Sammaan 915SCL27: both exchanges disagree with NSDL identically → one line, two links.
    const combined = verdictFor(
      [
        {
          ...base,
          source: "NSE",
          anchor: "due_date",
          notice_ids: ["n-nse"],
          fields: [
            f("due_date", "agree", "2026-09-28", ["2026-09-28"]),
            f("record_date", "different", "2026-09-13", ["2026-09-11"]),
          ],
        },
        {
          ...base,
          source: "BSE",
          anchor: "nse_record_date",
          notice_ids: ["n-bse"],
          fields: [
            f("due_date", "not_supplied", "2026-09-28"),
            f("record_date", "different", "2026-09-13", ["2026-09-11"]),
          ],
        },
      ],
      [
        { ...notice("n-nse", ["https://nsearchives.nseindia.com/a.pdf"]), source: "NSE" },
        notice("n-bse", ["https://www.bseindia.com/b"]),
      ],
    );
    expect(combined).toHaveLength(1);
    expect(combined[0].text).toBe(
      "NSE and BSE both say record date 11 Sept 2026 — NSDL has 13 Sept 2026",
    );
    expect(combined[0].tone).toBe("attention");
    expect(combined[0].links).toEqual([
      { label: "NSE", href: "https://nsearchives.nseindia.com/a.pdf" },
      { label: "BSE", href: "https://www.bseindia.com/b" },
    ]);

    // Different dates → not combined; two separate lines.
    const split = verdictFor(
      [
        {
          ...base,
          source: "NSE",
          anchor: "due_date",
          fields: [f("record_date", "different", "2026-09-13", ["2026-09-11"])],
        },
        {
          ...base,
          source: "BSE",
          fields: [f("record_date", "different", "2026-09-13", ["2026-09-12"])],
        },
      ],
      [],
    );
    expect(split).toHaveLength(2);
    expect(split[0].text).toMatch(/^NSE says/);
    expect(split[1].text).toMatch(/^BSE says/);
  });

  it("does not tie date evidence to the current displayed monetary amount", () => {
    expect(
      evidenceMatchesSchedule("TEST", "2026-09-11", [{ ...flow, amount: 91 }], evidence()),
    ).toBe(true);
  });
});

describe("evidence pairing ignores a moved record date", () => {
  it("still matches when the schedule carries the exchange record date", () => {
    const flows = [
      {
        event: "interest",
        due_date: "2026-10-28",
        record_date: "2026-10-13",
        payment_date: "2026-10-28",
      },
    ] as never;
    const evidence = {
      isin: "X",
      as_of: "2026-10-28",
      flows: [
        {
          key: "interest|2026-10-28|2026-10-21|2026-10-28",
          event: "interest",
          due_date: "2026-10-28",
          record_date: "2026-10-21",
          payment_date: "2026-10-28",
          ambiguous: false,
          checked: true,
          sources: [],
        },
      ],
    } as never;
    expect(evidenceMatchesSchedule("X", "2026-10-28", flows, evidence)).toBe(true);
    expect(flowKey(flows[0])).toBe("interest|2026-10-28|2026-10-28");
  });
});
