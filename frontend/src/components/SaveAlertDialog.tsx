import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { ExploreParams } from "../api/types";
import { describeRuleFilters, splitFilters, suggestRuleName } from "../lib/alertRules";

/** Turn the current Explore filters into an alert rule. The bond-describing
 * filters travel with the rule; trade-history filters are shown as dropped
 * so what gets watched is never a surprise. */
export default function SaveAlertDialog({
  params,
  onClose,
}: {
  params: ExploreParams;
  onClose: () => void;
}) {
  const { kept, dropped } = splitFilters(params);
  const [minYtm, setMinYtm] = useState("");
  const [maxTicket, setMaxTicket] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [minQty, setMinQty] = useState("");
  const [name, setName] = useState<string | null>(null);
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: () =>
      api.alerts.createRule({
        name: (name ?? suggestRuleName(kept, minYtm, maxPrice)).trim(),
        filters: kept,
        min_ask_ytm: minYtm ? Number(minYtm) : null,
        max_ticket: maxTicket ? Number(maxTicket) : null,
        max_price: maxPrice ? Number(maxPrice) : null,
        min_qty: minQty ? Number(minQty) : null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alerts"] });
      onClose();
    },
  });
  const valid =
    Boolean(minYtm || maxPrice) &&
    (name ?? suggestRuleName(kept, minYtm, maxPrice)).trim().length > 0;
  const input =
    "w-full rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none";

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/60" onClick={onClose} aria-hidden="true" />
      <div
        role="dialog"
        aria-label="Save as alert"
        className="fixed left-1/2 top-24 z-50 w-[28rem] -translate-x-1/2 rounded-lg border border-zinc-700 bg-zinc-900 p-5 shadow-2xl"
      >
        <h2 className="text-sm font-bold uppercase tracking-wider text-zinc-200">Save as alert</h2>
        <p className="mt-1 text-xs text-zinc-400">
          Watches every live ask in:{" "}
          <span className="text-zinc-200">{describeRuleFilters(kept)}</span>
        </p>
        {dropped.length > 0 && (
          <p className="mt-1 text-[11px] text-amber-400/90">
            Not applied to live asks: {dropped.join(", ")} — those describe the last trade, and a
            never-traded bond with a resting ask is exactly what should alert.
          </p>
        )}
        <div className="mt-4 grid grid-cols-2 gap-3">
          <label className="text-[11px] text-zinc-400">
            Min YTM @ Ask %
            <input
              className={`${input} mt-1`}
              inputMode="decimal"
              placeholder="12"
              value={minYtm}
              onChange={(e) => setMinYtm(e.target.value)}
            />
          </label>
          <label className="text-[11px] text-zinc-400">
            Max price per unit ₹
            <input
              className={`${input} mt-1`}
              inputMode="decimal"
              placeholder="optional"
              value={maxPrice}
              onChange={(e) => setMaxPrice(e.target.value)}
            />
          </label>
          <label className="text-[11px] text-zinc-400">
            Max ticket ₹ (price × qty)
            <input
              className={`${input} mt-1`}
              inputMode="decimal"
              placeholder="optional"
              value={maxTicket}
              onChange={(e) => setMaxTicket(e.target.value)}
            />
          </label>
          <label className="text-[11px] text-zinc-400">
            Min units at the ask
            <input
              className={`${input} mt-1`}
              inputMode="numeric"
              placeholder="1"
              value={minQty}
              onChange={(e) => setMinQty(e.target.value)}
            />
          </label>
        </div>
        <label className="mt-3 block text-[11px] text-zinc-400">
          Name
          <input
            className={`${input} mt-1`}
            maxLength={60}
            value={name ?? suggestRuleName(kept, minYtm, maxPrice)}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        {save.isError && (
          <p className="mt-2 text-xs text-red-400">{(save.error as Error).message}</p>
        )}
        <div className="mt-4 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 rounded-md border border-zinc-700 text-xs text-zinc-300 hover:border-zinc-500"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!valid || save.isPending}
            onClick={() => save.mutate()}
            className="px-3 py-1.5 rounded-md border border-emerald-500/60 text-xs font-medium text-emerald-400 hover:bg-emerald-500/10 disabled:opacity-40"
          >
            {save.isPending ? "Saving…" : "Save alert"}
          </button>
        </div>
        <p className="mt-3 text-[11px] text-zinc-600">
          Pushes go to the ntfy topic set under the bell → Alerts. Needs your Kite login for the
          day.
        </p>
      </div>
    </>
  );
}
