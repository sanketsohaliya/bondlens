import { useEffect, useMemo, useRef, useState } from "react";
import { fmt } from "../api/client";
import type { Bond } from "../api/types";
import { HOLDINGS_KEY, nextListName, useStoredList, useWatchlists } from "../lib/lists";
import CopyBtn from "./CopyBtn";
import RatingBadge from "./RatingBadge";

const monthYear = (d: string | null) =>
  d ? new Date(d).toLocaleDateString("en-IN", { month: "short", year: "numeric" }) : null;

const yearsMonths = (years: number) => {
  const months = Math.round(years * 12);
  const y = Math.floor(months / 12);
  const m = months % 12;
  if (y === 0) return `${m}mo`;
  if (m === 0) return `${y}y`;
  return `${y}y ${m}mo`;
};

type SortKey = keyof Pick<
  Bond,
  "ytm" | "ltp" | "turnover" | "years_to_maturity" | "rating_rank" | "days_since_traded"
>;

const COLS: { key: SortKey | null; label: string; right?: boolean }[] = [
  { key: null, label: "" },
  { key: null, label: "Symbol" },
  { key: "ytm", label: "YTM %" },
  { key: "ltp", label: "LTP" },
  { key: "turnover", label: "Retail turnover" },
  { key: "rating_rank", label: "Rating" },
  { key: "years_to_maturity", label: "Maturity", right: true },
  { key: "days_since_traded", label: "Last traded", right: true },
];

export default function ScreenerTable({
  bonds,
  loading,
  error,
  onSelect,
}: {
  bonds: Bond[];
  loading: boolean;
  error: string | null;
  onSelect: (isin: string, tab?: "ratings" | "chart") => void;
}) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "turnover", dir: -1 });
  const tableRef = useRef<HTMLTableElement>(null);
  const [widths, setWidths] = useState<number[] | null>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("screener-col-widths") ?? "null");
      return Array.isArray(saved) && saved.length === COLS.length ? saved : null;
    } catch {
      return null;
    }
  });

  const startResize = (i: number, e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const table = tableRef.current;
    if (!table) return;
    const ths = Array.from(table.querySelectorAll("thead th"));
    const base = widths ?? ths.map((t) => t.getBoundingClientRect().width);
    const startX = e.clientX;
    const startW = base[i];
    let next = base;
    const move = (ev: PointerEvent) => {
      next = [...base];
      next[i] = Math.max(48, startW + ev.clientX - startX);
      setWidths(next);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      localStorage.setItem("screener-col-widths", JSON.stringify(next));
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const sorted = useMemo(() => {
    const rows = [...bonds];
    rows.sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      if (av == null) return 1;
      if (bv == null) return -1;
      return (av < bv ? -1 : av > bv ? 1 : 0) * sort.dir;
    });
    return rows;
  }, [bonds, sort]);

  if (error) {
    return <div className="p-8 text-center text-red-400 text-sm">{error}</div>;
  }
  if (loading) {
    return (
      <div className="p-8 text-center text-zinc-500 text-sm animate-pulse">Loading bonds…</div>
    );
  }

  return (
    <div className="overflow-x-auto scroll-slim">
      <table ref={tableRef} className={`w-full text-sm ${widths ? "table-fixed" : ""}`}>
        {widths && (
          <colgroup>
            {widths.map((w, i) => (
              <col key={i} style={{ width: w }} />
            ))}
          </colgroup>
        )}
        <thead className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500 border-b-2 border-zinc-100">
          <tr>
            {COLS.map((c, i) => (
              <th
                key={i}
                className={`relative px-3 py-3 whitespace-nowrap ${c.right ? "text-right" : "text-left"} ${c.key ? "cursor-pointer select-none hover:text-zinc-300" : ""}`}
                onClick={() =>
                  c.key &&
                  setSort((s) => ({
                    key: c.key!,
                    dir: s.key === c.key ? ((s.dir * -1) as 1 | -1) : -1,
                  }))
                }
              >
                <span className={c.key === sort.key ? "text-zinc-200" : undefined}>{c.label}</span>
                {c.key === sort.key && (
                  <span className="ml-1 text-emerald-500/80">{sort.dir === -1 ? "▼" : "▲"}</span>
                )}
                {i > 0 && (
                  <span
                    className="absolute right-0 top-1/2 -translate-y-1/2 h-4 w-1.5 cursor-col-resize border-r-2 border-zinc-800 hover:border-emerald-500"
                    onPointerDown={(e) => startResize(i, e)}
                    onClick={(e) => e.stopPropagation()}
                    title="Drag to resize"
                  />
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((b) => (
            <Row key={b.isin} bond={b} onSelect={onSelect} />
          ))}
        </tbody>
      </table>
      {sorted.length === 0 && (
        <div className="p-8 text-center text-zinc-500 text-sm">No bonds match the filters.</div>
      )}
    </div>
  );
}

function Row({
  bond: b,
  onSelect,
}: {
  bond: Bond;
  onSelect: (isin: string, tab?: "ratings" | "chart") => void;
}) {
  return (
    <tr
      onClick={() => onSelect(b.isin)}
      className="group/row border-b border-zinc-900 hover:bg-zinc-900/60 cursor-pointer transition-colors"
    >
      <td className="pl-3 pr-1 py-2.5 w-14">
        <span className="flex items-center gap-1.5">
          <WatchStar isin={b.isin} />
          <HoldMark isin={b.isin} />
        </span>
      </td>
      <td className="px-3 py-2.5 w-80 max-w-80">
        <div className="flex items-center gap-1.5">
          <span className="font-medium text-zinc-200 hover:text-emerald-400 transition-colors">
            {b.nse_symbol ?? b.bse_scrip_id ?? b.isin}
          </span>
          <span className="flex items-center gap-1.5 opacity-0 group-hover/row:opacity-100 transition-opacity">
            <CopyBtn text={b.nse_symbol ?? b.bse_scrip_id ?? b.isin} label="symbol" />
            <CopyBtn text={b.isin} label="ISIN" chip="isin" />
          </span>
          {b.in_default && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/15 text-red-400 border border-red-500/30">
              DEFAULT
            </span>
          )}
        </div>
        <div className="text-[11px] text-zinc-500 truncate max-w-76">{b.issuer_name ?? b.isin}</div>
      </td>
      <td className="px-3 py-2.5 whitespace-nowrap">
        <span
          className={
            b.ytm_reliable === false
              ? "text-zinc-600 font-medium"
              : b.ytm != null
                ? "text-zinc-200 font-medium"
                : "text-zinc-500"
          }
          title={
            b.ytm_reliable === false
              ? b.coupon_basis?.startsWith("Variable")
                ? "Floating / market-linked coupon — YTM assumes the current rate holds; indicative only"
                : "Likely an annualization artifact (near maturity or out of band) — treat with caution"
              : b.ytm_method === "exact"
                ? "From the actual payment schedule"
                : b.ytm_method
                  ? "Estimated"
                  : undefined
          }
        >
          {fmt.pct(b.ytm)}
        </span>
        {b.ytm_method === "ytc" && (
          <span
            className="ml-1 text-[10px] text-zinc-500"
            title="Yield to earliest call (perpetual)"
          >
            YTC
          </span>
        )}
        {b.coupon_basis?.startsWith("Variable") && (
          <span className="ml-1 text-[10px] text-zinc-500" title="Floating / market-linked coupon">
            FLT
          </span>
        )}
      </td>
      <td
        className={`group/ltp px-3 py-2.5 whitespace-nowrap ${
          b.stale && (b.days_since_traded ?? 0) > 90 ? "text-amber-500/80" : "text-zinc-200"
        } font-medium`}
        onClick={(e) => {
          e.stopPropagation();
          onSelect(b.isin, "chart");
        }}
        title={
          [
            b.stale && b.days_since_traded != null
              ? `No trades seen for ${b.days_since_traded} days — this price and its YTM may be outdated`
              : null,
            b.face_value != null && b.ltp != null && b.ltp < b.face_value
              ? "Below face value"
              : null,
          ]
            .filter(Boolean)
            .join("\n") || undefined
        }
      >
        <div>
          <span className="group-hover/ltp:underline decoration-zinc-600 underline-offset-2">
            {fmt.inrFull(b.ltp)}
          </span>
          {!b.stale && b.ltp != null && b.prev_ltp != null && b.ltp !== b.prev_ltp && (
            <span
              className={`ml-1 text-[10px] ${
                b.ltp > b.prev_ltp ? "text-emerald-500/80" : "text-red-400/70"
              }`}
              title={`LTP on previous trading day: ${fmt.inrFull(b.prev_ltp)}`}
            >
              {b.ltp > b.prev_ltp ? "▲" : "▼"}
            </span>
          )}
          {b.stale && b.ltp != null && (
            <span className="ml-1 text-[10px] text-zinc-600 align-middle">⚠</span>
          )}
        </div>
        {b.face_value != null && (
          <div className="text-[11px] text-zinc-500 font-normal">
            FV {fmt.inrFull(b.face_value)}
          </div>
        )}
      </td>
      <td
        className="group/turnover px-3 py-2.5 whitespace-nowrap text-sm leading-snug"
        onClick={(e) => {
          e.stopPropagation();
          onSelect(b.isin, "chart");
        }}
      >
        {b.nse_turnover == null && b.bse_turnover == null ? (
          <span className="text-zinc-500">–</span>
        ) : (
          <>
            <span className="text-zinc-200 font-medium group-hover/turnover:underline decoration-zinc-600 underline-offset-2">
              {fmt.inr((b.nse_turnover ?? 0) + (b.bse_turnover ?? 0))}
            </span>
            {b.turnover != null && b.prev_turnover != null && b.turnover !== b.prev_turnover && (
              <span
                className={`ml-1 text-[10px] ${
                  b.turnover > b.prev_turnover ? "text-emerald-500/80" : "text-red-400/70"
                }`}
                title={`Turnover on previous trading day: ${fmt.inr(b.prev_turnover)}`}
              >
                {b.turnover > b.prev_turnover ? "▲" : "▼"}
              </span>
            )}
          </>
        )}
      </td>
      <td
        className="px-3 py-2.5 whitespace-nowrap hover:underline decoration-zinc-600 underline-offset-2"
        onClick={(e) => {
          e.stopPropagation();
          onSelect(b.isin, "ratings");
        }}
      >
        <RatingBadge
          rating={b.credit_rating}
          tone={b.rating_change === "down" ? "down" : b.rating_change === "up" ? "up" : undefined}
          toneTitle={
            b.rating_change === "down"
              ? `Downgraded${b.rating_changed_at ? ` on ${fmt.date(b.rating_changed_at)}` : ""} — was ${b.rating_prev}`
              : b.rating_change === "up"
                ? `Upgraded${b.rating_changed_at ? ` on ${fmt.date(b.rating_changed_at)}` : ""} — was ${b.rating_prev}`
                : undefined
          }
        />
      </td>
      <td className="px-3 py-2.5 whitespace-nowrap text-right">
        {b.maturity_date != null ? (
          <>
            <div className="text-zinc-200 font-medium">{monthYear(b.maturity_date)}</div>
            {b.years_to_maturity != null && (
              <div className="text-[11px] text-zinc-500">{yearsMonths(b.years_to_maturity)}</div>
            )}
          </>
        ) : b.perpetual ? (
          <span className="text-zinc-500">perp</span>
        ) : (
          <span className="text-zinc-500">–</span>
        )}
      </td>
      <td className="px-3 py-2.5 whitespace-nowrap text-right">
        <span className={b.stale ? "text-zinc-600" : "text-zinc-200 font-medium"}>
          {b.days_since_traded == null
            ? "never"
            : b.days_since_traded <= 1
              ? "today"
              : `${b.days_since_traded}d ago`}
        </span>
      </td>
    </tr>
  );
}

/** Star with a watchlist-picker popover: checkbox per list + inline
    "new list" creation. Filled while the bond is in at least one list. */
export function WatchStar({ isin }: { isin: string }) {
  const { lists, starred, toggle, create } = useWatchlists();
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const watched = starred(isin);

  useEffect(() => {
    if (!pos) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setPos(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPos(null);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [pos]);

  const submitDraft = () => {
    const name = draft?.trim();
    if (name) create(name, isin);
    setDraft(null);
  };

  return (
    <span ref={wrapRef} className="relative" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        onClick={(e) => {
          if (pos) {
            setPos(null);
            return;
          }
          const r = e.currentTarget.getBoundingClientRect();
          setPos({
            x: Math.max(8, Math.min(r.left, window.innerWidth - 232)),
            y: r.bottom + 6,
          });
          setDraft(lists.length === 0 ? "Watchlist 1" : null);
        }}
        className={`text-base leading-none ${watched ? "text-amber-400" : "text-zinc-700 hover:text-zinc-400"}`}
        title={watched ? "In your watchlists — click to manage" : "Add to a watchlist"}
      >
        {watched ? "★" : "☆"}
      </button>
      {pos && (
        <span
          className="fixed z-50 w-56 py-1 rounded-md border border-zinc-700 bg-zinc-900 shadow-xl text-left"
          style={{ left: pos.x, top: pos.y }}
        >
          {lists.map((l) => (
            <label
              key={l.id}
              className="flex items-center gap-2 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800 cursor-pointer"
            >
              <input
                type="checkbox"
                checked={l.isins.includes(isin)}
                onChange={() => toggle(l.id, isin)}
                className="accent-amber-400"
              />
              <span className="truncate">{l.name}</span>
            </label>
          ))}
          {draft == null ? (
            <button
              type="button"
              onClick={() => setDraft(nextListName(lists))}
              className="w-full px-3 py-1.5 text-left text-xs text-emerald-400 hover:bg-zinc-800"
            >
              + New list
            </button>
          ) : (
            <span className="block px-3 py-1.5">
              <input
                // biome-ignore lint/a11y/noAutofocus: the user just asked to type a list name
                autoFocus
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") submitDraft();
                  if (e.key === "Escape") {
                    e.stopPropagation();
                    setDraft(null);
                  }
                }}
                onBlur={submitDraft}
                placeholder="List name"
                className="w-full bg-transparent border border-zinc-600 rounded px-2 py-1 text-xs text-zinc-100 focus:outline-none focus:border-zinc-400"
              />
            </span>
          )}
        </span>
      )}
    </span>
  );
}

export function HoldMark({ isin }: { isin: string }) {
  const { has, toggle } = useStoredList(HOLDINGS_KEY);
  const held = has(isin);
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        toggle(isin);
      }}
      className={`text-sm leading-none ${held ? "text-amber-400" : "text-zinc-700 hover:text-zinc-400"}`}
      title={held ? "Remove from holdings" : "Add to holdings"}
    >
      {held ? "◆" : "◇"}
    </button>
  );
}
