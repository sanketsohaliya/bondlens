const COLORS: [number, string][] = [
  [0, "text-emerald-400 bg-emerald-500/10 border-emerald-500/30"], // AAA
  [3, "text-sky-400 bg-sky-500/10 border-sky-500/30"], // AA band
  [6, "text-amber-400 bg-amber-500/10 border-amber-500/30"], // A band
  [Infinity, "text-rose-400 bg-rose-500/10 border-rose-500/30"], // BBB & below
];

export default function RatingBadge({
  rating,
  rank,
}: {
  rating: string | null;
  rank: number | null;
}) {
  if (!rating) return <span className="text-zinc-600 text-xs">unrated</span>;
  const cls =
    rank == null
      ? "text-zinc-400 bg-zinc-500/10 border-zinc-500/30"
      : COLORS.find(([max]) => rank <= max)![1];
  const first = rating.split(",")[0].trim();
  return (
    <span
      className={`inline-block text-xs px-1.5 py-0.5 rounded border ${cls}`}
      title={rating}
    >
      {first}
    </span>
  );
}
