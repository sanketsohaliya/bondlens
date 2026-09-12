import { fmt } from "../api/client";
import type { YieldValuation } from "../api/types";
import { valuationTooltip } from "../lib/quoteValuation";

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
      {(value?.warnings?.length ?? 0) > 0 && (
        <span className="ml-1 text-[10px] text-amber-400">review</span>
      )}
      {stale && <span className="ml-1 text-[10px] text-zinc-500">snapshot</span>}
    </span>
  );
}
