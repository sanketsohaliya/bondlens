import { describe, expect, it, vi } from "vitest";
import { runAskAi } from "./askAi";

type Deps = Parameters<typeof runAskAi<string, string>>[0];

function deps(overrides: Partial<Deps> = {}) {
  const order: string[] = [];
  let resolveDetail!: (v: string) => void;
  const detail = new Promise<string>((r) => {
    resolveDetail = r;
  });
  const open = vi.fn<Deps["open"]>(() => {
    order.push("open");
    return {} as Window;
  });
  const loadDetail = vi.fn<Deps["loadDetail"]>(() => {
    order.push("fetch-start");
    return detail.then((p) => {
      order.push("fetch-done");
      return p;
    });
  });
  const buildPrompt = vi.fn<Deps["buildPrompt"]>((p) => `PROMPT(${p})`);
  const copy = vi.fn<Deps["copy"]>(async () => {
    order.push("copy");
  });
  const d: Deps = { open, loadDetail, buildPrompt, copy, ...overrides };
  return { d, mocks: { open, loadDetail, buildPrompt, copy }, order, resolveDetail };
}

describe("Ask AI click sequence", () => {
  it("opens the tab before the detail fetch resolves, then copies the built prompt", async () => {
    const { d, mocks, order, resolveDetail } = deps();
    const run = runAskAi(d);
    expect(order).toEqual(["open", "fetch-start"]);
    resolveDetail("bond");
    const result = await run;
    expect(order).toEqual(["open", "fetch-start", "fetch-done", "copy"]);
    expect(mocks.copy).toHaveBeenCalledWith("PROMPT(bond)");
    expect(result).toEqual({ status: "copied", opened: true });
  });

  it("still copies when the popup is blocked and reports it", async () => {
    const { d, mocks, resolveDetail } = deps({ open: () => null });
    const run = runAskAi(d);
    resolveDetail("bond");
    expect(await run).toEqual({ status: "copied", opened: false });
    expect(mocks.copy).toHaveBeenCalledTimes(1);
  });

  it("does not copy and reports failure when the detail fetch rejects", async () => {
    const { d, mocks } = deps({ loadDetail: () => Promise.reject(new Error("500")) });
    expect(await runAskAi(d)).toEqual({ status: "fetch-failed", opened: true });
    expect(mocks.copy).not.toHaveBeenCalled();
  });

  it("reports failure rather than hanging when the prompt builder throws", async () => {
    const { d, mocks, resolveDetail } = deps({
      buildPrompt: () => {
        throw new Error("malformed detail");
      },
    });
    const run = runAskAi(d);
    resolveDetail("bond");
    expect(await run).toEqual({ status: "fetch-failed", opened: true });
    expect(mocks.copy).not.toHaveBeenCalled();
  });

  it("hands the prompt back for manual copy when the clipboard write fails", async () => {
    const { d, resolveDetail } = deps({ copy: () => Promise.reject(new Error("denied")) });
    const run = runAskAi(d);
    resolveDetail("bond");
    expect(await run).toEqual({
      status: "clipboard-failed",
      opened: true,
      prompt: "PROMPT(bond)",
    });
  });
});
