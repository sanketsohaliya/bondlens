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
    expect(agree[0].link).toBe("https://www.bseindia.com/x");
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
    expect(both[0].link).toBeNull();

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
  });

  it("does not tie date evidence to the current displayed monetary amount", () => {
    expect(
      evidenceMatchesSchedule("TEST", "2026-09-11", [{ ...flow, amount: 91 }], evidence()),
    ).toBe(true);
  });
});
