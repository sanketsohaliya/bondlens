import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useBlacklist } from "../lib/blacklist";

/** The ⊘ beside a row's bookmark/star: "never auto-buy this bond".
 * Alerts for it still fire; the watcher refuses the order and records
 * "skipped - blacklisted". Click opens a small note editor. */
export default function NoAutoBuyMark({ isin }: { isin: string }) {
  const { entry, put, remove, ready } = useBlacklist(isin);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const [note, setNote] = useState("");
  const wrapRef = useRef<HTMLSpanElement>(null);
  const popRef = useRef<HTMLSpanElement>(null);
  const on = entry != null;

  useEffect(() => {
    if (!pos) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!wrapRef.current?.contains(t) && !popRef.current?.contains(t)) setPos(null);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setPos(null);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [pos]);

  if (!ready) return <span className="inline-block w-4" />;

  const open = (e: React.MouseEvent<HTMLButtonElement>) => {
    if (pos) {
      setPos(null);
      return;
    }
    const r = e.currentTarget.getBoundingClientRect();
    setNote(entry?.note ?? "");
    setPos({ x: Math.max(8, Math.min(r.left, window.innerWidth - 248)), y: r.bottom + 6 });
  };
  const save = () => {
    put(note);
    setPos(null);
  };

  return (
    <span ref={wrapRef} className="relative" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        onClick={open}
        className={`text-sm leading-none ${on ? "text-red-400" : "text-zinc-700 hover:text-zinc-400"}`}
        title={
          on
            ? `Never auto-buy${entry?.note ? `: ${entry.note}` : ""} — alerts still fire. Click to edit.`
            : "Never auto-buy this bond (alerts still fire)"
        }
        aria-pressed={on}
      >
        ⊘
      </button>
      {pos &&
        createPortal(
          <span
            ref={popRef}
            className="fixed z-50 w-60 rounded-md border border-zinc-700 bg-zinc-900 p-2.5 shadow-xl text-left text-xs"
            style={{ left: pos.x, top: pos.y }}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Enter") save();
            }}
          >
            <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
              Never auto-buy
            </div>
            <p className="mb-2 text-[11px] text-zinc-500">
              Alerts still fire; no order is placed for this bond by any rule.
            </p>
            <input
              // biome-ignore lint/a11y/noAutofocus: the user just opened this editor
              autoFocus
              value={note}
              maxLength={60}
              onChange={(e) => setNote(e.target.value)}
              placeholder="note (optional): thin book, already full…"
              className="w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-100 focus:border-emerald-500 focus:outline-none"
            />
            <div className="mt-2 flex items-center justify-between">
              {on ? (
                <button
                  type="button"
                  onClick={() => {
                    remove();
                    setPos(null);
                  }}
                  className="text-[11px] text-zinc-400 hover:text-zinc-200"
                >
                  Allow auto-buy again
                </button>
              ) : (
                <span />
              )}
              <button
                type="button"
                onClick={save}
                className="rounded bg-red-500/90 px-2 py-1 text-[11px] font-medium text-zinc-950 hover:bg-red-400"
              >
                {on ? "Save note" : "Never auto-buy"}
              </button>
            </div>
          </span>,
          document.body,
        )}
    </span>
  );
}
