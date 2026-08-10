import { type RefObject, useRef, useState } from "react";

/** Drag-resizable table columns with localStorage persistence (extracted
 * from ExploreTable so the holdings table can share it).
 *
 * Saved width arrays are discarded when the column count changes, so
 * adding/removing columns self-heals stale layouts. */
export function useColumnWidths(
  storageKey: string,
  colCount: number,
  opts?: { legacyKey?: string },
): {
  widths: number[] | null;
  tableRef: RefObject<HTMLTableElement | null>;
  startResize: (i: number, e: React.PointerEvent) => void;
} {
  const tableRef = useRef<HTMLTableElement>(null);
  const [widths, setWidths] = useState<number[] | null>(() => {
    try {
      const current = localStorage.getItem(storageKey);
      const legacy =
        current === null && opts?.legacyKey ? localStorage.getItem(opts.legacyKey) : null;
      const saved = JSON.parse(current ?? legacy ?? "null");
      if (legacy !== null && opts?.legacyKey) {
        localStorage.setItem(storageKey, legacy);
        localStorage.removeItem(opts.legacyKey);
      }
      return Array.isArray(saved) && saved.length === colCount ? saved : null;
    } catch {
      return null;
    }
  });

  const startResize = (i: number, e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const table = tableRef.current;
    if (!table) return;
    const ths = Array.from(table.querySelectorAll("thead th"));
    const base = widths ?? ths.map((t) => t.getBoundingClientRect().width);
    const startX = e.clientX;
    const startW = base[i];
    let next = base;
    const move = (ev: PointerEvent) => {
      next = [...base];
      next[i] = Math.max(48, startW + ev.clientX - startX);
      setWidths(next);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      localStorage.setItem(storageKey, JSON.stringify(next));
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return { widths, tableRef, startResize };
}
