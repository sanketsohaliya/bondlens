import { useState } from "react";
import { fmt } from "../api/client";
import type { LotInput, PortfolioLot, PortfolioPosition } from "../api/types";
import { useLotMutations } from "../lib/portfolio";

/** Expanded lots of one position: list + inline edit/delete + add. */
export default function LotsPanel({ p }: { p: PortfolioPosition }) {
  const { add, update, remove } = useLotMutations();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [confirmId, setConfirmId] = useState<number | null>(null);

  return (
    <div className="text-xs space-y-1">
      <div className="grid grid-cols-[1fr_1fr_1fr_1fr_auto] gap-2 text-[10px] text-zinc-500 px-1">
        <span>Units</span>
        <span>Price / unit</span>
        <span>Purchase date</span>
        <span>Platform</span>
        <span />
      </div>
      {p.lots.map((lot) =>
        editingId === lot.id ? (
          <LotEditor
            key={lot.id}
            initial={lot}
            pending={update.isPending}
            onSave={(input) =>
              update.mutate({ id: lot.id, lot: input }, { onSuccess: () => setEditingId(null) })
            }
            onCancel={() => setEditingId(null)}
          />
        ) : (
          <div
            key={lot.id}
            className="grid grid-cols-[1fr_1fr_1fr_1fr_auto] gap-2 items-center px-1 py-0.5 rounded hover:bg-zinc-800/60 group"
          >
            <span className="tabular-nums text-zinc-200">{fmt.num(lot.units, 2)}</span>
            <span className="tabular-nums">{fmt.inrFull(lot.price)}</span>
            <span>
              {lot.purchase_date ? new Date(lot.purchase_date).toLocaleDateString("en-IN") : "–"}
            </span>
            <span className="truncate">{lot.platform ?? "–"}</span>
            <span className="flex gap-1 opacity-0 group-hover:opacity-100">
              <button
                type="button"
                title="Edit lot"
                onClick={() => {
                  setConfirmId(null);
                  setEditingId(lot.id);
                }}
                className="px-1 text-zinc-500 hover:text-zinc-200"
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
                    : "px-1 text-zinc-500 hover:text-red-400"
                }
              >
                {confirmId === lot.id ? "sure?" : "✕"}
              </button>
            </span>
          </div>
        ),
      )}
      {adding ? (
        <LotEditor
          pending={add.isPending}
          onSave={(input) =>
            add.mutate({ isin: p.isin, lot: input }, { onSuccess: () => setAdding(false) })
          }
          onCancel={() => setAdding(false)}
        />
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="px-1 py-0.5 text-emerald-400 hover:text-emerald-300"
        >
          + Add lot
        </button>
      )}
    </div>
  );
}

function LotEditor({
  initial,
  pending,
  onSave,
  onCancel,
}: {
  initial?: PortfolioLot;
  pending: boolean;
  onSave: (input: LotInput) => void;
  onCancel: () => void;
}) {
  const [units, setUnits] = useState(initial ? String(initial.units) : "");
  const [price, setPrice] = useState(initial?.price != null ? String(initial.price) : "");
  const [date, setDate] = useState(initial?.purchase_date ?? "");
  const [platform, setPlatform] = useState(initial?.platform ?? "");
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const u = Number(units);
    if (!units || Number.isNaN(u) || u <= 0) {
      setError("Units must be positive");
      return;
    }
    const pr = price ? Number(price) : null;
    if (pr !== null && (Number.isNaN(pr) || pr <= 0)) {
      setError("Price must be positive");
      return;
    }
    onSave({
      units: u,
      price: pr,
      purchase_date: date || null,
      platform: platform.trim() || null,
    });
  };

  const field =
    "w-full bg-transparent border border-zinc-600 rounded px-1.5 py-0.5 text-xs " +
    "text-zinc-100 focus:outline-none focus:border-zinc-400 placeholder:text-zinc-600";

  return (
    <div className="px-1 py-1 space-y-1">
      <div
        className="grid grid-cols-[1fr_1fr_1fr_1fr_auto] gap-2 items-center"
        onKeyDown={(e) => {
          if (e.key === "Enter") submit();
          if (e.key === "Escape") onCancel();
        }}
      >
        <input
          // biome-ignore lint/a11y/noAutofocus: the user just opened this editor
          autoFocus
          inputMode="decimal"
          value={units}
          onChange={(e) => setUnits(e.target.value)}
          placeholder="units *"
          className={field}
        />
        <input
          inputMode="decimal"
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          placeholder="₹ / unit"
          className={field}
        />
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className={`${field} [color-scheme:dark]`}
        />
        <input
          value={platform}
          onChange={(e) => setPlatform(e.target.value)}
          placeholder="platform"
          maxLength={40}
          className={field}
        />
        <span className="flex gap-1 whitespace-nowrap">
          <button
            type="button"
            onClick={submit}
            disabled={pending}
            className="px-1.5 text-emerald-400 hover:text-emerald-300 disabled:opacity-50"
          >
            {pending ? "…" : "save"}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="px-1 text-zinc-500 hover:text-zinc-300"
          >
            cancel
          </button>
        </span>
      </div>
      {error && <div className="text-[10px] text-red-400">{error}</div>}
    </div>
  );
}
