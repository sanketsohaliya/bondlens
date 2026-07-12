/* v1 look: plain neutral text, no colored badge. Full rating string in the tooltip. */
export default function RatingBadge({ rating }: { rating: string | null }) {
  if (!rating) return <span className="text-zinc-600 text-xs">unrated</span>;
  const first = rating.split(",")[0].trim();
  return (
    <span className="text-xs text-zinc-200" title={rating}>
      {first}
    </span>
  );
}
