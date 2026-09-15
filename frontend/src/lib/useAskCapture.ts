// One capture of every listed bond's best ask per Explore page load.
//
// The explorer filters client-side, so the ask set must cover the WHOLE
// universe or a bond can be missed simply because it was outside the
// filtered view (or past a budget) when the asks were fetched. The
// server pages the universe (1,000 bonds, two paced Kite calls each);
// this hook walks the pages once and merges them as they land. Nothing
// polls afterwards: what you see is the book as it stood when the page
// loaded, labelled with its capture time. Reload to capture again.
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { AskYtm } from "../api/types";

export interface AskCapture {
  /** isin -> best ask, for every bond that had one. */
  asks: Map<string, AskYtm>;
  /** Pages merged so far / total pages (null until the first answers). */
  done: number;
  pages: number | null;
  /** True while pages are still arriving. */
  loading: boolean;
  error: string | null;
  retry: () => void;
}

const idle = () => ({
  asks: new Map<string, AskYtm>(),
  done: 0,
  pages: null as number | null,
  loading: false,
  error: null as string | null,
});

export function useAskCapture(enabled: boolean, userId: number, today: string): AskCapture {
  const [state, setState] = useState(idle);
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: userId/today/attempt restart the capture on purpose
  useEffect(() => {
    if (!enabled) {
      setState(idle());
      return;
    }
    let cancelled = false;
    setState({ ...idle(), loading: true });
    (async () => {
      let page = 0;
      let pages = 1;
      while (page < pages) {
        let r: Awaited<ReturnType<typeof api.kite.askYtmPage>>;
        try {
          r = await api.kite.askYtmPage(page);
        } catch (e) {
          if (!cancelled)
            setState((s) => ({ ...s, loading: false, error: (e as Error).message || "failed" }));
          return;
        }
        if (cancelled) return;
        pages = r.pages ?? 1;
        const merged = r.items;
        setState((s) => {
          const asks = new Map(s.asks);
          for (const item of merged) asks.set(item.isin, item);
          return { ...s, asks, done: page + 1, pages };
        });
        page += 1;
      }
      if (!cancelled) setState((s) => ({ ...s, loading: false }));
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled, userId, today, attempt]);

  return { ...state, retry };
}
