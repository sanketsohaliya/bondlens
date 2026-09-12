import { useEffect, useId, useRef, useState } from "react";
import Tip from "./Tooltip";

export type StatDetail = { title: string; lines: string[] };

/** Compact stat card shared by the Overview and Charts tabs — one place
    to tweak the box styling so the two tabs can't drift apart. */
export default function StatBox({
  label,
  value,
  accent,
  badge,
  tip,
  detail,
  className,
}: {
  label: string;
  value: string;
  /** Emerald value — Overview's key numbers (YTM etc.). */
  accent?: boolean;
  /** Amber pill next to the label (e.g. ex-interest). */
  badge?: string;
  /** Hover card with the full text; renders an ⓘ hint on the label. */
  tip?: string;
  /** Longer explanation opened by clicking the ⓘ: one paragraph per line.
   * Independent of the hover tip, which stays short. */
  detail?: StatDetail;
  /** Extra classes from the caller (grid col-span etc.). */
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  const hint = detail ? (
    <button
      type="button"
      aria-expanded={open}
      aria-controls={panelId}
      aria-label={`${open ? "Hide" : "Show"} ${detail.title.toLowerCase()}`}
      onClick={(e) => {
        e.stopPropagation();
        setOpen((o) => !o);
      }}
      className={`ml-1 rounded px-0.5 leading-none transition-colors ${open ? "text-emerald-400" : "text-zinc-600 hover:text-zinc-300"}`}
    >
      ⓘ
    </button>
  ) : (
    tip && <span className="ml-1 text-zinc-600">ⓘ</span>
  );

  const card = (
    <div
      className={`rounded-lg border border-zinc-800 bg-zinc-900/50 px-3 py-2 h-full ${detail ? "" : (className ?? "")}`}
    >
      <div className="text-[10px] text-zinc-500">
        {label}
        {badge && (
          <span className="ml-1.5 text-[9px] font-medium px-1 py-0.5 rounded bg-amber-500/15 text-amber-400 border border-amber-500/30">
            {badge}
          </span>
        )}
        {hint}
      </div>
      <div
        className={`text-xs font-semibold mt-0.5 break-words line-clamp-2 ${
          accent ? "text-emerald-400" : "text-zinc-200"
        }`}
      >
        {value}
      </div>
    </div>
  );
  const body = tip ? (
    <Tip text={tip} className="contents">
      {card}
    </Tip>
  ) : (
    card
  );
  if (!detail) return body;
  return (
    <div ref={rootRef} className={`relative h-full ${className ?? ""}`}>
      {body}
      {open && (
        <div
          id={panelId}
          role="region"
          aria-label={detail.title}
          className="absolute left-0 top-full z-40 mt-1 w-[min(28rem,90vw)] rounded-lg border border-zinc-700 bg-zinc-950 p-3 text-xs text-zinc-300 shadow-xl space-y-1.5"
        >
          <div className="text-[10px] uppercase tracking-wide text-zinc-500">{detail.title}</div>
          {detail.lines.map((line, i) => (
            <p key={`${i}-${line.slice(0, 24)}`}>{line}</p>
          ))}
        </div>
      )}
    </div>
  );
}
