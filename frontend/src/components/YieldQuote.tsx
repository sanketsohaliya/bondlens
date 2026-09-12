import { fmt } from "../api/client";
import type { YieldValuation } from "../api/types";
import {
  hasYieldReview,
  isMarketComparison,
  valuationTooltip,
  yieldReviewWarnings,
} from "../lib/quoteValuation";

export default function YieldQuote({
  value,
  stale = false,
}: {
  value?: YieldValuation | null;
  stale?: boolean;
}) {
  const available = !stale && value?.ytm != null && Number.isFinite(value.ytm);
  const inferred =
    value?.estimated || value?.ytm_method === "estimated" || value?.ytm_method === "approx";
  return (
    <span
      title={
        stale
          ? "Quote or calculation snapshot expired; refresh for a current valuation."
          : valuationTooltip(value)
      }
    >
      {available ? `${inferred ? "~" : ""}${fmt.pct(value?.ytm)}` : "–"}
      {available && (value?.yield_type === "ytc" || value?.ytm_method === "ytc") && (
        <span className="ml-1 text-[10px] text-zinc-500">YTC</span>
      )}
      {hasYieldReview(value) && <span className="ml-1 text-[10px] text-amber-400">review</span>}
      {stale && <span className="ml-1 text-[10px] text-zinc-500">snapshot</span>}
    </span>
  );
}

export function YieldNotes({
  value,
  reviewTitle = "Yield review notes",
}: {
  value?: YieldValuation | null;
  reviewTitle?: string;
}) {
  const reviews = yieldReviewWarnings(value);
  const comparisons = (value?.warnings ?? []).filter(isMarketComparison);
  return (
    <>
      {reviews.length > 0 && (
        <details className="rounded border border-amber-500/30 p-3 text-xs text-amber-300">
          <summary className="cursor-pointer">
            {reviewTitle} ({reviews.length})
          </summary>
          <ul className="mt-2 list-disc pl-4 space-y-1">
            {reviews.map((note, index) => (
              <li key={`${note.code}:${index}`}>{note.message}</li>
            ))}
          </ul>
        </details>
      )}
      {comparisons.length > 0 && (
        <details className="rounded border border-zinc-800 p-3 text-xs text-zinc-400">
          <summary className="cursor-pointer">Market comparison</summary>
          <ul className="mt-2 list-disc pl-4 space-y-1">
            {comparisons.map((note, index) => (
              <li key={`${note.code}:${index}`}>{note.message}</li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}
