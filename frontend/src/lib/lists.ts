// Per-browser bond lists (watchlist / holdings) in localStorage.
//
// Each browser tracks its own lists — no server state, no auth. The hook
// is built on useSyncExternalStore: the native `storage` event covers
// other tabs, and a same-tab custom event covers writes from this tab
// (the browser deliberately doesn't fire `storage` at the writer).
import { useCallback, useSyncExternalStore } from "react";

export const WATCHLIST_KEY = "bondlens.watchlist";
export const HOLDINGS_KEY = "bondlens.holdings";

const LOCAL_EVENT = "bondlens:lists";

// Cached snapshots so useSyncExternalStore gets referentially-stable
// values between changes (a fresh array every render would loop forever).
const cache = new Map<string, { raw: string | null; items: string[] }>();

function read(key: string): string[] {
  const raw = localStorage.getItem(key);
  const hit = cache.get(key);
  if (hit && hit.raw === raw) return hit.items;
  let items: string[] = [];
  if (raw) {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) items = parsed.filter((x) => typeof x === "string");
    } catch {
      /* corrupted entry — treat as empty */
    }
  }
  cache.set(key, { raw, items });
  return items;
}

function write(key: string, items: string[]) {
  localStorage.setItem(key, JSON.stringify(items));
  window.dispatchEvent(new CustomEvent(LOCAL_EVENT, { detail: key }));
}

function subscribe(key: string, onChange: () => void): () => void {
  const onStorage = (e: StorageEvent) => {
    if (e.key === null || e.key === key) onChange();
  };
  const onLocal = (e: Event) => {
    if ((e as CustomEvent).detail === key) onChange();
  };
  window.addEventListener("storage", onStorage);
  window.addEventListener(LOCAL_EVENT, onLocal);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(LOCAL_EVENT, onLocal);
  };
}

/** A localStorage-backed ISIN list, live across components and tabs. */
export function useStoredList(key: string) {
  const items = useSyncExternalStore(
    useCallback((cb) => subscribe(key, cb), [key]),
    () => read(key),
  );
  const has = useCallback((isin: string) => items.includes(isin), [items]);
  const toggle = useCallback(
    (isin: string) => {
      const cur = read(key);
      write(key, cur.includes(isin) ? cur.filter((x) => x !== isin) : [...cur, isin]);
    },
    [key],
  );
  return { items, has, toggle };
}
