import { useState } from "react";
import { fmt } from "../api/client";
import type { LotInput, PortfolioLot, PortfolioPosition } from "../api/types";
import { useLotMutations } from "../lib/portfolio";
import { MetadataSeparator } from "./InlineMetadata";

const TH = "px-2 py-1 text-[10px] font-medium text-zinc-500 whitespace-nowrap";
const TD = "px-2 py-1.5 whitespace-nowrap";

/** One position's lots: the two facts that distinguish them (when, at
 * what price) and how each has done since. Add / edit use one labeled
 * form under the table. Rendered inside the "Your lots" card. */
export default function LotsPanel({
  p,
  platforms = [],
}: {
  p: PortfolioPosition;
  /** Platforms used across the portfolio - suggestions for the form. */
  platforms?: string[];
}) {
  const { add, update, remove } = useLotMutations();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [confirmId, setConfirmId] = useState<number | null>(null);

  return (
    <div className="text-xs">
      <table className="w-full">
        <thead>
          <tr className="border-b border-zinc-800/60 text-left">
            <th className={TH}>Bought</th>
            <th className={`${TH} text-right`}>Units</th>
            <th className={`${TH} text-right`}>Paid</th>
            <th className={`${TH} text-right`}>P&L</th>
            <th className={`${TH} text-right`} title="Projected XIRR on this lot alone">
              XIRR
            </th>
            <th className={TH} />
          </tr>
        </thead>
        <tbody>
          {p.lots.map((lot) => (
            <tr
              key={lot.id}
              title={lot.platform ? `Bought on ${lot.platform}` : undefined}
              className={`border-b border-zinc-800/40 last:border-0 hover:bg-zinc-800/40 ${
                editingId === lot.id ? "bg-zinc-800/40" : ""
              }`}
            >
              <td className={`${TD} text-zinc-300`}>{fmt.date(lot.purchase_date)}</td>
              <td className={`${TD} text-right tabular-nums text-zinc-200`}>
                {fmt.num(lot.units, 2)}
              </td>
              <td className={`${TD} text-right tabular-nums`}>{fmt.inrFull(lot.price)}</td>
              <td className={`${TD} text-right tabular-nums`}>
                {lot.gain == null ? (
                  <span className="text-zinc-600">–</span>
                ) : (
                  <span className={lot.gain >= 0 ? "text-emerald-400" : "text-red-400"}>
                    {lot.gain >= 0 ? "+" : "−"}
                    {fmt.inr(Math.abs(lot.gain))}
                    {lot.gain_pct != null && (
                      <span className="text-[10px] opacity-80"> ({lot.gain_pct.toFixed(1)}%)</span>
                    )}
                  </span>
                )}
              </td>
              <td className={`${TD} text-right tabular-nums`}>{fmt.pct(lot.xirr)}</td>
              <td className={`${TD} text-right`}>
                <span className="inline-flex gap-1">
                  <button
                    type="button"
                    title="Edit lot"
                    onClick={() => {
                      setConfirmId(null);
                      setAdding(false);
                      setEditingId(lot.id);
                    }}
                    className="px-1 text-zinc-600 hover:text-zinc-200"
                  >
                    ✎
                  </button>
                  <button
                    type="button"
                    title={confirmId === lot.id ? "Click again to delete" : "Delete lot"}
                    onClick={() => {
                      if (confirmId === lot.id) {
                        remove.mutate(lot.id);
                        setConfirmId(null);
                      } else {
                        setConfirmId(lot.id);
                      }
                    }}
                    className={
                      confirmId === lot.id
                        ? "px-1 text-red-400"
                        : "px-1 text-zinc-600 hover:text-red-400"
                    }
                  >
                    {confirmId === lot.id ? "sure?" : "✕"}
                  </button>
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {editingId != null && (
        <LotEditor
          key={editingId}
          initial={p.lots.find((l) => l.id === editingId)}
          platforms={platforms}
          pending={update.isPending}
          onSave={(input) =>
            update.mutate({ id: editingId, lot: input }, { onSuccess: () => setEditingId(null) })
          }
          onCancel={() => setEditingId(null)}
        />
      )}
      {adding && (
        <LotEditor
          platforms={platforms}
          pending={add.isPending}
          onSave={(input) =>
            add.mutate({ isin: p.isin, lot: input }, { onSuccess: () => setAdding(false) })
          }
          onCancel={() => setAdding(false)}
        />
      )}

      <div className="mt-2 flex items-center justify-between text-[11px] text-zinc-500">
        <span>
          {fmt.inr(p.invested)} invested
          {p.avg_price != null && (
            <>
              <MetadataSeparator />
              avg {fmt.inrFull(p.avg_price)}
            </>
          )}
        </span>
        {!adding && editingId == null && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="text-emerald-400 hover:text-emerald-300"
          >
            + Add lot
          </button>
        )}
      </div>
    </div>
  );
}

const todayIso = () =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());

/** Add / edit form: labeled fields, today's date by default, platform
 * suggestions, a live "invested" preview and per-field errors. */
export function LotEditor({
  initial,
  platforms = [],
  pending,
  onSave,
  onCancel,
}: {
  initial?: PortfolioLot;
  platforms?: string[];
  pending: boolean;
  onSave: (input: LotInput) => void;
  onCancel: () => void;
}) {
  const [units, setUnits] = useState(initial ? String(initial.units) : "");
  const [price, setPrice] = useState(initial?.price != null ? String(initial.price) : "");
  const [date, setDate] = useState(initial?.purchase_date ?? todayIso());
  const [platform, setPlatform] = useState(initial?.platform ?? "");
  const [errors, setErrors] = useState<{ units?: string; price?: string; date?: string }>({});

  const u = Number(units);
  const pr = Number(price);
  const unitsOk = units !== "" && Number.isFinite(u) && u > 0;
  const priceOk = price !== "" && Number.isFinite(pr) && pr > 0;
  const invested = unitsOk && priceOk ? u * pr : null;

  const submit = () => {
    const next: typeof errors = {};
    if (!unitsOk) next.units = "Units must be a positive number";
    if (!priceOk) next.price = "Price per unit is required";
    if (!date) next.date = "Purchase date is required";
    setErrors(next);
    if (Object.keys(next).length) return;
    onSave({ units: u, price: pr, purchase_date: date, platform: platform.trim() || null });
  };

  const field =
    "w-full bg-zinc-950 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-100 " +
    "focus:outline-none focus:border-emerald-500 placeholder:text-zinc-600";
  const label = "block text-[10px] text-zinc-500 mb-0.5";
  const err = (m?: string) => m && <div className="mt-0.5 text-[10px] text-red-400">{m}</div>;
  const listId = `platforms-${initial?.id ?? "new"}`;

  return (
    <div
      className="mt-2 rounded-md border border-zinc-800 bg-zinc-950/60 p-3"
      onKeyDown={(e) => {
        if (e.key === "Enter") submit();
        if (e.key === "Escape") onCancel();
      }}
    >
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
        {initial ? `Edit lot · bought ${fmt.date(initial.purchase_date)}` : "Add lot"}
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <div>
          <label className={label} htmlFor={`${listId}-date`}>
            Bought
          </label>
          <input
            id={`${listId}-date`}
            type="date"
            required
            value={date}
            max={todayIso()}
            onChange={(e) => setDate(e.target.value)}
            className={`${field} [color-scheme:dark]`}
          />
          {err(errors.date)}
        </div>
        <div>
          <label className={label} htmlFor={`${listId}-units`}>
            Units
          </label>
          <input
            id={`${listId}-units`}
            // biome-ignore lint/a11y/noAutofocus: the user just opened this form
            autoFocus
            inputMode="decimal"
            value={units}
            onChange={(e) => setUnits(e.target.value)}
            placeholder="e.g. 2"
            className={field}
          />
          {err(errors.units)}
        </div>
        <div>
          <label className={label} htmlFor={`${listId}-price`}>
            Paid ₹ / unit
          </label>
          <input
            id={`${listId}-price`}
            inputMode="decimal"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            placeholder="e.g. 1,95,000"
            className={field}
          />
          {err(errors.price)}
        </div>
        <div>
          <label className={label} htmlFor={`${listId}-platform`}>
            Platform <span className="text-zinc-600">(optional)</span>
          </label>
          <input
            id={`${listId}-platform`}
            list={listId}
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
            placeholder="Zerodha, IndiaBonds…"
            maxLength={40}
            className={field}
          />
          <datalist id={listId}>
            {platforms.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        </div>
      </div>
      <div className="mt-2 flex items-center justify-between">
        <span className="text-[11px] text-zinc-500 tabular-nums">
          {invested != null ? `= ${fmt.inr(invested)} invested` : "= – invested"}
        </span>
        <span className="flex items-center gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="px-2 py-1 text-[11px] text-zinc-400 hover:text-zinc-200"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={pending}
            className="rounded bg-emerald-500/90 px-2.5 py-1 text-[11px] font-medium text-zinc-950 hover:bg-emerald-400 disabled:opacity-50"
          >
            {pending ? "Saving…" : "Save lot"}
          </button>
        </span>
      </div>
    </div>
  );
}
