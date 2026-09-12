/** The "Ask AI" click, as a pure sequence with injected effects so the
 * ordering guarantee is unit-testable.
 *
 * `open` MUST run synchronously first: browsers only allow `window.open`
 * inside the user gesture, and any `await` before it turns the new tab into a
 * blocked popup. The bond detail (needed to build the prompt) is fetched after
 * the tab is already open, then the prompt is copied. */

export type AskAiResult<P = string> =
  | { status: "copied"; opened: boolean }
  | { status: "fetch-failed"; opened: boolean }
  | { status: "clipboard-failed"; opened: boolean; prompt: P };

export async function runAskAi<D, P = string>(deps: {
  open: () => Window | null;
  loadDetail: () => Promise<D>;
  buildPrompt: (detail: D) => P;
  copy: (prompt: P) => Promise<void>;
}): Promise<AskAiResult<P>> {
  const opened = deps.open() != null;
  let prompt: P;
  try {
    // Building the prompt is inside the same guard: a malformed detail must
    // surface as a failure, never leave the button stuck in its working state.
    prompt = deps.buildPrompt(await deps.loadDetail());
  } catch {
    return { status: "fetch-failed", opened };
  }
  try {
    await deps.copy(prompt);
  } catch {
    return { status: "clipboard-failed", opened, prompt };
  }
  return { status: "copied", opened };
}
