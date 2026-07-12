/* Plain neutral text, one line per agency: "CRISIL AA+/Stable". */
export default function RatingBadge({ rating }: { rating: string | null }) {
  if (!rating) return <span className="text-zinc-600 text-xs">unrated</span>;
  return (
    <div className="text-xs leading-snug" title={rating}>
      {rating.split(", ").map((entry) => {
        const i = entry.indexOf(" ");
        const agency = i > 0 ? entry.slice(0, i) : null;
        const value = i > 0 ? entry.slice(i + 1) : entry;
        return (
          <div key={entry}>
            {agency && <span className="text-zinc-500">{agency} </span>}
            <span className="text-zinc-200">{value}</span>
          </div>
        );
      })}
    </div>
  );
}
