import { useEffect, useState } from "react";
import { fmt } from "../api/client";
import type { Slice } from "../lib/portfolio";

// Others (last slice when grouped) intentionally gets the muted zinc.
const PALETTE = [
  "#34d399", // emerald
  "#38bdf8", // sky
  "#fbbf24", // amber
  "#a78bfa", // violet
  "#fb7185", // rose
  "#2dd4bf", // teal
  "#fb923c", // orange
  "#818cf8", // indigo
  "#a3e635", // lime
  "#a1a1aa", // zinc
];

export function sliceColor(i: number, label: string): string {
  return label === "Others" ? PALETTE[9] : PALETTE[i % PALETTE.length];
}

/** Interactive SVG donut: hover highlights a slice and swaps the center
    label; clicking selects it (caller filters the holdings table). */
export default function Donut({
  title,
  slices,
  selected,
  onSlice,
}: {
  title: string;
  slices: Slice[];
  /** Currently selected slice label (external state), if any. */
  selected?: string | null;
  onSlice?: (label: string | null) => void;
}) {
  const [hovered, setHovered] = useState<number | null>(null);
  // Draw-in: dasharray transitions from 0-length on mount.
  const [drawn, setDrawn] = useState(false);
  useEffect(() => {
    const t = requestAnimationFrame(() => setDrawn(true));
    return () => cancelAnimationFrame(t);
  }, []);

  const total = slices.reduce((s, x) => s + x.value, 0);
  const active = hovered != null ? slices[hovered] : null;
  const R = 15.9155; // circumference 100 => dasharray works in percents

  let start = 0;
  const arcs = slices.map((s, i) => {
    const arc = { slice: s, i, offset: start };
    start += s.pct;
    return arc;
  });

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
      <h3 className="text-xs font-bold text-zinc-200 uppercase tracking-wider mb-3">{title}</h3>
      <div className="flex items-center gap-4">
        <div className="relative shrink-0">
          <svg viewBox="0 0 42 42" width="132" height="132" role="img" aria-label={title}>
            <circle cx="21" cy="21" r={R} fill="none" stroke="#27272a" strokeWidth="4" />
            {arcs.map(({ slice, i, offset }) => (
              <circle
                key={slice.label}
                cx="21"
                cy="21"
                r={R}
                fill="none"
                stroke={sliceColor(i, slice.label)}
                strokeWidth={hovered === i || selected === slice.label ? 6 : 4}
                strokeDasharray={
                  drawn
                    ? `${Math.max(slice.pct - 0.4, 0.2)} ${100 - Math.max(slice.pct - 0.4, 0.2)}`
                    : "0 100"
                }
                strokeDashoffset={-offset}
                transform="rotate(-90 21 21)"
                className="cursor-pointer transition-all duration-500"
                opacity={selected && selected !== slice.label ? 0.35 : 1}
                onMouseEnter={() => setHovered(i)}
                onMouseLeave={() => setHovered(null)}
                onClick={() => onSlice?.(selected === slice.label ? null : slice.label)}
              />
            ))}
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center text-center pointer-events-none px-4">
            <div className="text-[10px] text-zinc-500 truncate max-w-full">
              {active ? active.label : "Total"}
            </div>
            <div className="text-xs font-semibold text-zinc-100">
              {fmt.inr(active ? active.value : total)}
            </div>
            {active && <div className="text-[10px] text-zinc-400">{active.pct}%</div>}
          </div>
        </div>
        <ul className="flex-1 min-w-0 space-y-1">
          {slices.map((s, i) => (
            <li key={s.label}>
              <button
                type="button"
                onMouseEnter={() => setHovered(i)}
                onMouseLeave={() => setHovered(null)}
                onClick={() => onSlice?.(selected === s.label ? null : s.label)}
                className={`w-full flex items-center gap-2 text-left text-[11px] rounded px-1 py-0.5 transition-colors ${
                  hovered === i || selected === s.label
                    ? "bg-zinc-800 text-zinc-100"
                    : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                <span
                  className="w-2 h-2 rounded-full shrink-0"
                  style={{ background: sliceColor(i, s.label) }}
                />
                <span className="truncate flex-1">{s.label}</span>
                <span className="tabular-nums text-zinc-500">{s.pct}%</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
