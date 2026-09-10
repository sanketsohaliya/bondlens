/* Rating value with the agency name on a sub-line (same treatment as the
   FV sub-line under LTP). The colon separates agency from rating in the
   raw string — agency names can be multi-word. */
export default function RatingBadge({
  rating,
  showAgency = true,
  tone,
  toneTitle,
  agencySuffix,
}: {
  rating: string | null;
  showAgency?: boolean;
  /** Recent rating action: "down" adds a red ▼, "up" a green ▲ — same
      palette as the explorer's price/turnover arrows. */
  tone?: "down" | "up";
  /** Tooltip for the ▲/▼ change arrow (e.g. "Upgraded on … — was …"). */
  toneTitle?: string;
  /** Appended to the first agency sub-line (e.g. the secured "(S)"/"(U)"
      marker) — keeps extra facts on the existing line, not a new one. */
  agencySuffix?: React.ReactNode;
}) {
  if (!rating) return <span className="text-zinc-600 text-xs">unrated</span>;
  const entries = rating.split(", ");
  const shown = entries.length > 2 ? entries.slice(0, 2) : entries;
  const hidden = entries.length - shown.length;
  return (
    <div className="text-sm leading-snug">
      {shown.map((entry, idx) => {
        const i = entry.indexOf(": ");
        const agency = i > 0 ? entry.slice(0, i) : null;
        const value = i > 0 ? entry.slice(i + 2) : entry;
        const suffix = idx === 0 ? agencySuffix : null;
        return (
          <div key={entry}>
            <div>
              <span className="text-zinc-200 font-medium">{value}</span>
              {tone === "down" && (
                <span className="ml-1 text-[10px] text-red-400/70 align-middle" title={toneTitle}>
                  ▼
                </span>
              )}
              {tone === "up" && (
                <span
                  className="ml-1 text-[10px] text-emerald-500/80 align-middle"
                  title={toneTitle}
                >
                  ▲
                </span>
              )}
              {hidden > 0 && entry === shown[shown.length - 1] && (
                <span className="ml-1.5 text-[10px] text-zinc-600">+{hidden}</span>
              )}
            </div>
            {showAgency && (agency || suffix) && (
              <div className="text-[11px] text-zinc-500 font-normal">
                {agency}
                {suffix}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
