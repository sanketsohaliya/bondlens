import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { AlertRule, ExploreParams } from "../api/types";
import { describeRuleFilters, splitFilters, suggestRuleName } from "../lib/alertRules";

const num = (v: number | null | undefined) => (v == null ? "" : String(v));

/** Create a rule from the current Explore filters, or edit an existing one.
 * Creating: bond-describing filters travel with the rule and trade-history
 * filters are listed as dropped. Editing: filters are read-only (they come
 * from the bar), thresholds and name change in place. */
export default function SaveAlertDialog({
  params,
  rule,
  onClose,
}: {
  params?: ExploreParams;
  rule?: AlertRule;
  onClose: () => void;
}) {
  const editing = rule != null;
  const { kept, dropped } = editing
    ? { kept: rule.filters ?? {}, dropped: [] as string[] }
    : splitFilters(params ?? {});
  const [minYtm, setMinYtm] = useState(num(rule?.min_ask_ytm));
  const [maxTicket, setMaxTicket] = useState(num(rule?.max_ticket));
  const [maxPrice, setMaxPrice] = useState(num(rule?.max_price));
  const [minQty, setMinQty] = useState(num(rule?.min_qty));
  const [name, setName] = useState<string | null>(rule?.name ?? null);
  const qc = useQueryClient();
  const finalName = () => (name ?? suggestRuleName(kept, minYtm, maxPrice)).trim();
  const body = () => ({
    name: finalName(),
    min_ask_ytm: minYtm ? Number(minYtm) : null,
    max_ticket: maxTicket ? Number(maxTicket) : null,
    max_price: maxPrice ? Number(maxPrice) : null,
    min_qty: minQty ? Number(minQty) : null,
  });
  const save = useMutation({
    mutationFn: () =>
      editing
        ? api.alerts.updateRule(rule.id, body())
        : api.alerts.createRule({ ...body(), filters: kept }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alerts"] });
      onClose();
    },
  });
  const valid = Boolean(minYtm || maxPrice) && finalName().length > 0;
  const input =
    "w-full rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none";

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/60" onClick={onClose} aria-hidden="true" />
      <div
        role="dialog"
        aria-label={editing ? "Edit alert" : "Save as alert"}
        className="fixed left-1/2 top-24 z-50 w-[28rem] -translate-x-1/2 rounded-lg border border-zinc-700 bg-zinc-900 p-5 shadow-2xl"
      >
        <h2 className="text-sm font-bold uppercase tracking-wider text-zinc-200">
          {editing ? "Edit alert" : "Save as alert"}
        </h2>
        <p className="mt-1 text-xs text-zinc-400">
          Watches every live ask in:{" "}
          <span className="text-zinc-200">{describeRuleFilters(kept)}</span>
          {editing && (
            <span className="block mt-0.5 text-[11px] text-zinc-600">
              Filters come from the Explore bar — to change them, set the bar and save a new rule.
            </span>
          )}
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
            {save.isPending ? "Saving…" : editing ? "Save changes" : "Save alert"}
          </button>
        </div>
        {!editing && (
          <p className="mt-3 text-[11px] text-zinc-600">
            Pushes go to the ntfy topic under Alert ▾ → ⚙. Needs your Kite login for the day.
          </p>
        )}
      </div>
    </>
  );
}
