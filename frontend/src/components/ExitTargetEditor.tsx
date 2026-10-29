import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, fmt } from "../api/client";

/** Exit target for a holding: the watcher pushes when the best BID
 * reaches the price, or implies a yield at or below the threshold. Lives
 * in the holding's expanded row on Portfolio. */
export default function ExitTargetEditor({
  isin,
  compact = false,
}: {
  isin: string;
  /** Inside the Exit panel the heading is already "Target": no label. */
  compact?: boolean;
}) {
  const qc = useQueryClient();
  const overview = useQuery({ queryKey: ["alerts"], queryFn: () => api.alerts.overview() });
  const current = overview.data?.targets.find((t) => t.isin === isin) ?? null;
  const [editing, setEditing] = useState(false);
  const [price, setPrice] = useState("");
  const [ytm, setYtm] = useState("");
  const invalidate = () => qc.invalidateQueries({ queryKey: ["alerts"] });
  const save = useMutation({
    mutationFn: () =>
      api.alerts.putTarget(isin, {
        min_bid_price: price ? Number(price) : null,
        max_bid_ytm: ytm ? Number(ytm) : null,
      }),
    onSuccess: () => {
      setEditing(false);
      invalidate();
    },
  });
  const remove = useMutation({
    mutationFn: () => api.alerts.deleteTarget(isin),
    onSuccess: invalidate,
  });
  const input =
    "w-28 rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-100 focus:border-emerald-500 focus:outline-none";

  const begin = () => {
    setPrice(current?.min_bid_price != null ? String(current.min_bid_price) : "");
    setYtm(current?.max_bid_ytm != null ? String(current.max_bid_ytm) : "");
    setEditing(true);
  };

  return (
    <div
      className="flex flex-wrap items-center gap-2 text-xs text-zinc-400"
      onClick={(e) => e.stopPropagation()}
    >
      {!compact && (
        <span className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
          Exit target
        </span>
      )}
      {!editing && current && (
        <>
          <span className="text-zinc-200">
            {[
              current.min_bid_price != null ? `bid ≥ ${fmt.inrFull(current.min_bid_price)}` : null,
              current.max_bid_ytm != null ? `bid YTM ≤ ${current.max_bid_ytm}%` : null,
            ]
              .filter(Boolean)
              .join(" or ")}
          </span>
          <span className="text-emerald-500/80" title="The watcher is armed for this bond">
            ● armed
          </span>
          <button type="button" onClick={begin} className="underline hover:text-zinc-200">
            edit
          </button>
          <button
            type="button"
            onClick={() => remove.mutate()}
            className="underline hover:text-red-400"
          >
            remove
          </button>
        </>
      )}
      {!editing && !current && (
        <button type="button" onClick={begin} className="underline hover:text-zinc-200">
          {compact
            ? "none - set a bid price or yield"
            : "set a bid price or yield to be told when you can sell"}
        </button>
      )}
      {editing && (
        <>
          <input
            className={input}
            inputMode="decimal"
            placeholder="min bid ₹"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
          />
          <span className="text-zinc-600">or</span>
          <input
            className={input}
            inputMode="decimal"
            placeholder="max bid YTM %"
            value={ytm}
            onChange={(e) => setYtm(e.target.value)}
          />
          <button
            type="button"
            disabled={(!price && !ytm) || save.isPending}
            onClick={() => save.mutate()}
            className="px-2 py-1 rounded-md border border-emerald-500/60 text-emerald-400 hover:bg-emerald-500/10 disabled:opacity-40"
          >
            Save
          </button>
          <button
            type="button"
            onClick={() => setEditing(false)}
            className="px-2 py-1 rounded-md border border-zinc-700 hover:border-zinc-500"
          >
            Cancel
          </button>
          {save.isError && <span className="text-red-400">{(save.error as Error).message}</span>}
        </>
      )}
    </div>
  );
}
