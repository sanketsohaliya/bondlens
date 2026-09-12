/** The "Ask AI" click, as a pure sequence with injected effects so the
 * ordering guarantee is unit-testable.
 *
 * Order is fetch → build → copy → open. Browsers grant `clipboard.writeText`
 * only during transient user activation (~1s in Safari, ~5s elsewhere), and
 * the bond-detail fetch is 0–200ms, so copying first keeps the write inside
 * that window. The tab opens last: a blocked popup with the prompt already on
 * the clipboard is recoverable in one click, whereas an open tab with nothing
 * to paste is not. Callers prefetch the detail on hover so the common case has
 * no await at all. */

export type AskAiResult<P = string> =
  | { status: "copied"; opened: boolean }
  | { status: "fetch-failed"; opened: false }
  | { status: "clipboard-failed"; opened: false; prompt: P };

export async function runAskAi<D, P = string>(deps: {
  open: () => Window | null;
  loadDetail: () => Promise<D>;
  buildPrompt: (detail: D) => P;
  copy: (prompt: P) => Promise<void>;
}): Promise<AskAiResult<P>> {
  let prompt: P;
  try {
    prompt = deps.buildPrompt(await deps.loadDetail());
  } catch {
    return { status: "fetch-failed", opened: false };
  }
  try {
    await deps.copy(prompt);
  } catch {
    return { status: "clipboard-failed", opened: false, prompt };
  }
  return { status: "copied", opened: deps.open() != null };
}
