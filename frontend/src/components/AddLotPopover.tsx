import { useState } from "react";
import type { LotInput } from "../api/types";
import { useLotMutations } from "../lib/portfolio";

/** Ask App to switch to the portfolio page (fired from deep components —
    an event beats prop-drilling through table rows and the drawer). */
export const GOTO_PORTFOLIO_EVENT = "bondlens:goto-portfolio";

/** Small fixed-position form: units, price and purchase date required;
    platform optional. Used by the ◆ marker in the table and the drawer. */
export default function AddLotPopover({
  isin,
  held,
  pos,
  onClose,
}: {
  isin: string;
  held: boolean;
  pos: { x: number; y: number };
  onClose: () => void;
}) {
  const { add } = useLotMutations();
  const [units, setUnits] = useState("");
  const [price, setPrice] = useState("");
  const [date, setDate] = useState("");
  const [platform, setPlatform] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const u = Number(units);
    if (!units || Number.isNaN(u) || u <= 0) {
      setError("Units must be a positive number");
      return;
    }
    const p = Number(price);
    if (!price || Number.isNaN(p) || p <= 0) {
      setError("Price paid per unit is required");
      return;
    }
    if (!date) {
      setError("Purchase date is required");
      return;
    }
    const lot: LotInput = {
      units: u,
      price: p,
      purchase_date: date,
      platform: platform.trim() || null,
    };
    add.mutate(
      { isin, lot },
      {
        onSuccess: onClose,
        onError: (e) => setError(e.message),
      },
    );
  };

  const field =
    "w-full bg-transparent border border-zinc-600 rounded px-2 py-1 text-xs " +
    "text-zinc-100 focus:outline-none focus:border-zinc-400 placeholder:text-zinc-600";

  return (
    <span
      className="fixed z-50 w-60 p-3 rounded-md border border-zinc-700 bg-zinc-900 shadow-xl block text-left"
      style={{ left: pos.x, top: pos.y }}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
        if (e.key === "Enter") submit();
      }}
    >
      <div className="flex items-baseline justify-between mb-2">
        <span className="text-xs font-semibold text-zinc-200">Add to portfolio</span>
        {held && (
          <button
            type="button"
            onClick={() => {
              onClose();
              window.dispatchEvent(new Event(GOTO_PORTFOLIO_EVENT));
            }}
            className="text-[10px] text-emerald-400 hover:text-emerald-300"
          >
            in portfolio — manage →
          </button>
        )}
      </div>
      <div className="space-y-2">
        <label className="block">
          <span className="block text-[10px] text-zinc-500 mb-0.5">Units *</span>
          <input
            // biome-ignore lint/a11y/noAutofocus: the user just opened this form to type
            autoFocus
            inputMode="decimal"
            value={units}
            onChange={(e) => setUnits(e.target.value)}
            placeholder="e.g. 10"
            className={field}
          />
        </label>
        <label className="block">
          <span className="block text-[10px] text-zinc-500 mb-0.5">Price paid / unit (₹) *</span>
          <input
            inputMode="decimal"
            required
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            placeholder="e.g. 1000"
            className={field}
          />
        </label>
        <div className="flex gap-2">
          <label className="block flex-1">
            <span className="block text-[10px] text-zinc-500 mb-0.5">Purchase date *</span>
            <input
              type="date"
              required
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className={`${field} [color-scheme:dark]`}
            />
          </label>
          <label className="block flex-1">
            <span className="block text-[10px] text-zinc-500 mb-0.5">Platform</span>
            <input
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
              placeholder="optional"
              maxLength={40}
              className={field}
            />
          </label>
        </div>
        {error && <div className="text-[10px] text-red-400">{error}</div>}
        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            className="px-2 py-1 text-xs text-zinc-400 hover:text-zinc-200"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={add.isPending}
            className="px-2.5 py-1 text-xs font-medium rounded border border-emerald-500/50 text-emerald-400 hover:bg-emerald-500/10 disabled:opacity-50"
          >
            {add.isPending ? "Adding…" : "Add lot"}
          </button>
        </div>
      </div>
    </span>
  );
}
