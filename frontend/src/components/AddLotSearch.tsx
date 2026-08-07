import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api/client";
import { useHeldIsins } from "../lib/lists";
import AddLotPopover from "./AddLotPopover";
import RatingBadge from "./RatingBadge";

/** "+ Add lot" for the Holdings header: search any bond (backend q search —
 * name/symbol/ISIN/rating), pick one, then the standard AddLotPopover form. */
export default function AddLotSearch() {
  const { has } = useHeldIsins();
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const [picked, setPicked] = useState<string | null>(null);

  const close = () => {
    setPos(null);
    setPicked(null);
  };

  return (
    <span onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        onClick={(e) => {
          if (pos) {
            close();
            return;
          }
          const r = e.currentTarget.getBoundingClientRect();
          setPos({
            x: Math.max(8, Math.min(r.right - 288, window.innerWidth - 296)),
            y: r.bottom + 6,
          });
        }}
        className="px-2 py-1 text-[11px] font-medium rounded border border-emerald-500/50 text-emerald-400 hover:bg-emerald-500/10 normal-case tracking-normal"
      >
        + Add lot
      </button>
      {pos &&
        createPortal(
          <>
            <span className="fixed inset-0 z-40 block" onClick={close} aria-hidden="true" />
            {picked ? (
              <AddLotPopover isin={picked} held={has(picked)} pos={pos} onClose={close} />
            ) : (
              <BondSearch pos={pos} onPick={setPicked} onClose={close} />
            )}
          </>,
          document.body,
        )}
    </span>
  );
}

function BondSearch({
  pos,
  onPick,
  onClose,
}: {
  pos: { x: number; y: number };
  onPick: (isin: string) => void;
  onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);

  const { data, isFetching } = useQuery({
    queryKey: ["addlot-search", debounced],
    queryFn: () => api.bonds({ q: debounced, limit: 8 }),
    enabled: debounced.length >= 2,
  });
  const results = debounced.length >= 2 ? (data?.bonds ?? []) : [];

  return (
    <span
      className="fixed z-50 w-72 p-3 rounded-md border border-zinc-700 bg-zinc-900 shadow-xl block text-left"
      style={{ left: pos.x, top: pos.y }}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
        if (e.key === "ArrowDown") {
          e.preventDefault();
          setActive((a) => Math.min(a + 1, results.length - 1));
        }
        if (e.key === "ArrowUp") {
          e.preventDefault();
          setActive((a) => Math.max(a - 1, 0));
        }
        if (e.key === "Enter" && results[active]) onPick(results[active].isin);
      }}
    >
      <div className="text-xs font-semibold text-zinc-200 mb-2">Add lot — pick a bond</div>
      <input
        // biome-ignore lint/a11y/noAutofocus: the user just opened this form to type
        autoFocus
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setActive(0);
        }}
        placeholder="Search company, symbol, ISIN"
        className="w-full bg-transparent border border-zinc-600 rounded px-2 py-1 text-xs text-zinc-100 focus:outline-none focus:border-zinc-400 placeholder:text-zinc-600"
      />
      <div ref={listRef} className="mt-2 max-h-64 overflow-y-auto scroll-slim">
        {debounced.length < 2 ? (
          <div className="py-3 text-center text-[11px] text-zinc-600">
            Type at least 2 characters
          </div>
        ) : isFetching && results.length === 0 ? (
          <div className="py-3 text-center text-[11px] text-zinc-500 animate-pulse">Searching…</div>
        ) : results.length === 0 ? (
          <div className="py-3 text-center text-[11px] text-zinc-600">No bonds found</div>
        ) : (
          results.map((b, i) => (
            <button
              key={b.isin}
              type="button"
              onClick={() => onPick(b.isin)}
              onMouseEnter={() => setActive(i)}
              className={`w-full flex items-center gap-2 px-2 py-1.5 rounded text-left text-xs ${
                i === active ? "bg-zinc-800 text-zinc-100" : "text-zinc-300"
              }`}
            >
              <span className="min-w-0 flex-1">
                <span className="block font-medium truncate">
                  {b.nse_symbol ?? b.bse_scrip_id ?? b.isin}
                </span>
                <span className="block text-[10px] text-zinc-500 truncate">{b.issuer_name}</span>
              </span>
              <RatingBadge rating={b.credit_rating} showAgency={false} />
            </button>
          ))
        )}
      </div>
    </span>
  );
}
