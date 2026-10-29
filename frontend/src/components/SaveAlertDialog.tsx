import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { AlertRule, ExploreParams } from "../api/types";
import { previewLabel, splitFilters } from "../lib/alertRules";

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
  const [minTicket, setMinTicket] = useState(num(rule?.min_ticket));
  const [buyBudget, setBuyBudget] = useState(num(rule?.buy_budget));
  const [buyCap, setBuyCap] = useState(num(rule?.buy_daily_cap));
  const qc = useQueryClient();
  const body = () => ({
    min_ask_ytm: minYtm ? Number(minYtm) : null,
    min_ticket: minTicket ? Number(minTicket) : null,
    buy_budget: buyBudget ? Number(buyBudget) : null,
    buy_daily_cap: buyCap ? Number(buyCap) : null,
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
  const autoBuyHalf = Boolean(buyBudget) !== Boolean(buyCap);
  const valid = Boolean(minYtm) && !autoBuyHalf;
  const label = previewLabel({ filters: kept, ...body() });
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
          Set the ask conditions; the filters come from the Explore bar.
          {editing && " To change filters, set the bar and save a new rule."}
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
          <label
            className="text-[11px] text-zinc-400"
            title="Only asks with at least this much on offer (best-ask price × quantity) count."
          >
            Min ticket ₹ (price × qty)
            <input
              className={`${input} mt-1`}
              inputMode="decimal"
              placeholder="optional, e.g. 50000"
              value={minTicket}
              onChange={(e) => setMinTicket(e.target.value)}
            />
          </label>
        </div>
        <div className="mt-4 rounded-md border border-amber-500/20 bg-amber-500/5 p-3">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-amber-400/90">
            Auto-buy (optional)
          </div>
          <p className="mt-0.5 text-[11px] text-zinc-500">
            When armed, a hit places a LIMIT CNC buy at the ask: as many units as the budget
            affords, never more than are offered. Arming is per day, from the rule row.
          </p>
          <div className="mt-2 grid grid-cols-2 gap-3">
            <label className="text-[11px] text-zinc-400">
              Buy up to ₹ per hit
              <input
                className={`${input} mt-1`}
                inputMode="decimal"
                placeholder="e.g. 10000"
                value={buyBudget}
                onChange={(e) => setBuyBudget(e.target.value)}
              />
            </label>
            <label className="text-[11px] text-zinc-400">
              Daily cap ₹ for this rule
              <input
                className={`${input} mt-1`}
                inputMode="decimal"
                placeholder="e.g. 50000"
                value={buyCap}
                onChange={(e) => setBuyCap(e.target.value)}
              />
            </label>
          </div>
          {autoBuyHalf && <p className="mt-1 text-[11px] text-amber-400">Set both, or neither.</p>}
        </div>
        <div className="mt-3 text-[11px] text-zinc-400">
          Shown everywhere as
          <div className="mt-1 rounded-md border border-zinc-800 bg-zinc-950/60 px-2 py-1.5 text-xs text-zinc-200 leading-snug">
            {label}
          </div>
        </div>
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
