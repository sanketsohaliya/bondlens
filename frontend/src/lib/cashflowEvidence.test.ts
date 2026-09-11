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

  it("does not tie date evidence to the current displayed monetary amount", () => {
    expect(
      evidenceMatchesSchedule("TEST", "2026-09-11", [{ ...flow, amount: 91 }], evidence()),
    ).toBe(true);
  });
});
