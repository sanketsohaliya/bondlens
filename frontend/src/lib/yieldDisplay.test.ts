import { describe, expect, it } from "vitest";
import { firstFutureCall, isYieldToCall, yieldLabel } from "./yieldDisplay";

const bond = {
  as_of: "2026-09-11",
  maturity_date: "2028-09-11",
  ytm: 10,
  ytm_exact: 10,
  ytm_approx: null,
  amounts_estimated: false,
  call_dates: [] as string[],
};

describe("yield presentation", () => {
  it("does not label missing yields as approximate", () => {
    expect(yieldLabel({ ...bond, ytm: null, ytm_exact: null })).toBe("Yield unavailable");
  });

  it("distinguishes published and estimated schedules", () => {
    expect(yieldLabel(bond)).toBe("YTM (exact)");
    expect(yieldLabel({ ...bond, amounts_estimated: true })).toBe("YTM (estimated)");
    expect(yieldLabel({ ...bond, ytm_exact: null, ytm_approx: 10 })).toBe("YTM (approx)");
  });

  it("labels a perpetual call yield as YTC even with a plain schedule shape", () => {
    const callable = {
      ...bond,
      maturity_date: null,
      ytm_exact: null,
      call_dates: ["2027-12-14", "2025-12-14", "2026-12-14"],
    };
    expect(isYieldToCall(callable)).toBe(true);
    expect(yieldLabel(callable)).toBe("YTC");
    expect(firstFutureCall(callable)).toBe("2026-12-14");
    expect(callable.call_dates[0]).toBe("2027-12-14");
  });
});
