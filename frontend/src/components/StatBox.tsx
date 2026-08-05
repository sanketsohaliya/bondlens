import Tip from "./Tooltip";

/** Compact stat card shared by the Overview and Charts tabs — one place
    to tweak the box styling so the two tabs can't drift apart. */
export default function StatBox({
  label,
  value,
  accent,
  badge,
  tip,
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
  /** Extra classes from the caller (grid col-span etc.). */
  className?: string;
}) {
  const card = (
    <div
      className={`rounded-lg border border-zinc-800 bg-zinc-900/50 px-3 py-2 h-full ${className ?? ""}`}
    >
      <div className="text-[10px] text-zinc-500">
        {label}
        {badge && (
          <span className="ml-1.5 text-[9px] font-medium px-1 py-0.5 rounded bg-amber-500/15 text-amber-400 border border-amber-500/30">
            {badge}
          </span>
        )}
        {tip && <span className="ml-1 text-zinc-600">ⓘ</span>}
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
  return tip ? (
    <Tip text={tip} className="contents">
      {card}
    </Tip>
  ) : (
    card
  );
}
