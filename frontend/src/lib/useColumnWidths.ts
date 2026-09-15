import { type CSSProperties, type RefObject, useEffect, useRef, useState } from "react";

/** Drag-resizable table columns with localStorage persistence (shared by
 * the Explore and holdings tables).
 *
 * Widths are saved per column COUNT. The Explore table has 8 columns
 * until Kite status arrives and 9 once connected; a single saved array
 * keyed on the table alone went stale the moment the layout changed,
 * and a stale array is worse than none: the render gate rejected it,
 * every drag re-derived from it and re-saved it, and no column ever
 * moved. Now each layout keeps its own widths, storage is re-read when
 * the count changes, and a drag always starts from what is on screen. */

export function storageKeyFor(storageKey: string, colCount: number): string {
  return `${storageKey}:${colCount}`;
}

/** Saved widths are trusted only when they match the live header count. */
export function pickBase(saved: number[] | null, measured: number[]): number[] {
  return saved && saved.length === measured.length ? saved : measured;
}

function readWidths(storageKey: string, colCount: number, legacyKey?: string): number[] | null {
  try {
    const key = storageKeyFor(storageKey, colCount);
    let raw = localStorage.getItem(key);
    if (raw === null) {
      // Older builds saved one unkeyed array (and, before that, under
      // the pre-rename key); adopt it once if it fits this layout.
      for (const old of [storageKey, legacyKey]) {
        if (!old) continue;
        const candidate = localStorage.getItem(old);
        if (candidate === null) continue;
        localStorage.removeItem(old);
        const parsed = JSON.parse(candidate);
        if (Array.isArray(parsed) && parsed.length === colCount) {
          raw = candidate;
          localStorage.setItem(key, candidate);
          break;
        }
      }
    }
    const saved = raw === null ? null : JSON.parse(raw);
    return Array.isArray(saved) && saved.length === colCount && saved.every(Number.isFinite)
      ? saved
      : null;
  } catch {
    return null;
  }
}

export function useColumnWidths(
  storageKey: string,
  colCount: number,
  opts?: { legacyKey?: string },
): {
  widths: number[] | null;
  tableRef: RefObject<HTMLTableElement | null>;
  startResize: (i: number, e: React.PointerEvent) => void;
  /** Spread onto <table>: fixed layout sized to the columns once any
   * width is set, so a drag moves that column 1:1 instead of being
   * absorbed by its neighbours (the wrapper scrolls horizontally). */
  tableProps: { className: string; style?: CSSProperties };
} {
  const tableRef = useRef<HTMLTableElement>(null);
  const legacyKey = opts?.legacyKey;
  const [widths, setWidths] = useState<number[] | null>(() =>
    readWidths(storageKey, colCount, legacyKey),
  );
  // The count can change after mount (Kite column appearing): switch to
  // that layout's own saved widths rather than carrying the old array.
  useEffect(() => {
    setWidths(readWidths(storageKey, colCount, legacyKey));
  }, [storageKey, colCount, legacyKey]);

  const startResize = (i: number, e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const table = tableRef.current;
    if (!table) return;
    const ths = Array.from(table.querySelectorAll("thead th"));
    if (i < 0 || i >= ths.length) return;
    const base = pickBase(
      widths,
      ths.map((t) => t.getBoundingClientRect().width),
    );
    const startX = e.clientX;
    const startW = base[i];
    let next = base;
    const handle = e.currentTarget;
    try {
      handle.setPointerCapture(e.pointerId);
    } catch {
      /* not all environments support capture; window listeners suffice */
    }
    const move = (ev: PointerEvent) => {
      next = [...base];
      next[i] = Math.max(48, Math.round(startW + ev.clientX - startX));
      setWidths(next);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      try {
        localStorage.setItem(storageKeyFor(storageKey, next.length), JSON.stringify(next));
      } catch {
        /* storage unavailable - the layout still applies for this session */
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  };

  const sized = widths != null && widths.length === colCount;
  const tableProps = sized
    ? {
        className: "table-fixed min-w-full",
        style: { width: widths.reduce((s, w) => s + w, 0) },
      }
    : { className: "w-full" };

  return { widths: sized ? widths : null, tableRef, startResize, tableProps };
}
