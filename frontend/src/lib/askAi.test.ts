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
  it("copies the prompt before opening the tab, so the clipboard write is inside user activation", async () => {
    const { d, mocks, order, resolveDetail } = deps();
    const run = runAskAi(d);
    expect(order).toEqual(["fetch-start"]);
    resolveDetail("bond");
    const result = await run;
    expect(order).toEqual(["fetch-start", "fetch-done", "copy", "open"]);
    expect(mocks.copy).toHaveBeenCalledWith("PROMPT(bond)");
    expect(result).toEqual({ status: "copied", opened: true });
  });

  it("reports a blocked popup but the copy has already succeeded", async () => {
    const { d, mocks, order, resolveDetail } = deps({ open: () => null });
    const run = runAskAi(d);
    resolveDetail("bond");
    expect(await run).toEqual({ status: "copied", opened: false });
    expect(mocks.copy).toHaveBeenCalledTimes(1);
    expect(order.indexOf("copy")).toBeLessThan(order.length);
  });

  it("neither copies nor opens when the detail fetch rejects", async () => {
    const { d, mocks } = deps({ loadDetail: () => Promise.reject(new Error("500")) });
    expect(await runAskAi(d)).toEqual({ status: "fetch-failed", opened: false });
    expect(mocks.copy).not.toHaveBeenCalled();
    expect(mocks.open).not.toHaveBeenCalled();
  });

  it("neither copies nor opens when the prompt builder throws", async () => {
    const { d, mocks, resolveDetail } = deps({
      buildPrompt: () => {
        throw new Error("malformed detail");
      },
    });
    const run = runAskAi(d);
    resolveDetail("bond");
    expect(await run).toEqual({ status: "fetch-failed", opened: false });
    expect(mocks.copy).not.toHaveBeenCalled();
    expect(mocks.open).not.toHaveBeenCalled();
  });

  it("hands the prompt back for manual copy and does not open a tab when the clipboard write fails", async () => {
    const { d, mocks, resolveDetail } = deps({ copy: () => Promise.reject(new Error("denied")) });
    const run = runAskAi(d);
    resolveDetail("bond");
    expect(await run).toEqual({
      status: "clipboard-failed",
      opened: false,
      prompt: "PROMPT(bond)",
    });
    expect(mocks.open).not.toHaveBeenCalled();
  });
});
