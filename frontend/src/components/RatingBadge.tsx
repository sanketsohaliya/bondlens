/* Plain neutral text, one line per agency: "CRISIL: AA+/Stable". The colon
   separates agency from rating — agency names can be multi-word. */
export default function RatingBadge({ rating }: { rating: string | null }) {
  if (!rating) return <span className="text-zinc-600 text-xs">unrated</span>;
  const entries = rating.split(", ");
  const shown = entries.length > 2 ? entries.slice(0, 2) : entries;
  const hidden = entries.length - shown.length;
  return (
    <div className="text-sm leading-snug" title={rating}>
      {shown.map((entry) => {
        const i = entry.indexOf(": ");
        const agency = i > 0 ? entry.slice(0, i) : null;
        const value = i > 0 ? entry.slice(i + 2) : entry;
        return (
          <div key={entry}>
            <span className="text-zinc-200">{value}</span>
            {agency && (
              <span className="ml-1.5 text-[10px] uppercase tracking-wide text-zinc-500">
                {agency}
              </span>
            )}
            {hidden > 0 && entry === shown[shown.length - 1] && (
              <span className="ml-1.5 text-[10px] text-zinc-600">+{hidden}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}
