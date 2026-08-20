import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, fmt } from "../api/client";
import type { AskYtm, Bond } from "../api/types";
import { nextListName, useHeldIsins, useWatchlists } from "../lib/lists";
import { useColumnWidths } from "../lib/useColumnWidths";
import AddLotPopover from "./AddLotPopover";
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

type SortKey =
  | keyof Pick<Bond, "ytm" | "turnover" | "years_to_maturity" | "rating_rank" | "days_since_traded">
  | "ask_ytm"; // client-side only — computed from the user's Kite session

type Col = { key: SortKey | null; label: string; right?: boolean };

const BASE_COLS: Col[] = [
  { key: null, label: "" },
  { key: null, label: "Symbol" },
  { key: "ytm", label: "YTM @ LTP" },
  { key: "turnover", label: "Retail turnover" },
  { key: "rating_rank", label: "Rating" },
  { key: "years_to_maturity", label: "Maturity", right: true },
  { key: "days_since_traded", label: "Last traded", right: true },
];

/** With Kite connected, YTM @ Ask slots in right after YTM @ LTP. */
const KITE_COLS: Col[] = [
  ...BASE_COLS.slice(0, 3),
  { key: "ask_ytm", label: "YTM @ Ask" },
  ...BASE_COLS.slice(3),
];

const COLUMN_WIDTHS_KEY = "explore-col-widths";
const LEGACY_COLUMN_WIDTHS_KEY = "screener-col-widths";

/* Last-known asks, so reloads paint straight into the live order instead
   of reordering when the fetch lands. 30 min: fresh enough to predict
   the current order, expired by the next session (no stale-ask sorting,
   no snapshot→ytm re-shift after hours). */
const ASK_SNAPSHOT_KEY = "ask-ytm-snapshot";
const ASK_SNAPSHOT_TTL = 30 * 60 * 1000;
const ASK_SNAPSHOT_CAP = 2000;

function loadAskSnapshot(): Map<string, AskYtm> | null {
  try {
    const raw = localStorage.getItem(ASK_SNAPSHOT_KEY);
    if (!raw) return null;
    const { ts, items } = JSON.parse(raw) as { ts: number; items: AskYtm[] };
    if (Date.now() - ts > ASK_SNAPSHOT_TTL) return null;
    return new Map(items.map((i) => [i.isin, i]));
  } catch {
    return null;
  }
}

function saveAskSnapshot(fresh: AskYtm[]) {
  try {
    const merged = loadAskSnapshot() ?? new Map<string, AskYtm>();
    for (const item of fresh) merged.set(item.isin, item);
    const items = [...merged.values()].slice(-ASK_SNAPSHOT_CAP);
    localStorage.setItem(ASK_SNAPSHOT_KEY, JSON.stringify({ ts: Date.now(), items }));
  } catch {
    /* storage full/unavailable — snapshot is only an optimization */
  }
}

export type SortState = { key: SortKey; dir: 1 | -1 };

export default function ExploreTable({
  bonds,
  loading,
  error,
  initialSort,
  onSelect,
  onSortChange,
}: {
  bonds: Bond[];
  loading: boolean;
  error: string | null;
  onSelect: (isin: string, tab?: "ratings" | "chart" | "order book") => void;
  initialSort?: SortState;
  onSortChange?: (s: SortState) => void;
}) {
  const [sort, setSortState] = useState<SortState>(initialSort ?? { key: "ask_ytm", dir: -1 });
  const setSort = (update: (s: SortState) => SortState) =>
    setSortState((s) => {
      const next = update(s);
      onSortChange?.(next);
      return next;
    });

  // YTM @ Ask: best-ask yields fetched under the user's own Kite session.
  const { data: kite } = useQuery({
    queryKey: ["kite-status"],
    queryFn: api.kite.status,
    staleTime: 120_000,
    refetchOnWindowFocus: false,
  });
  const kiteConnected = kite?.connected ?? false;
  const isins = useMemo(() => bonds.map((b) => b.isin), [bonds]);
  const askYtm = useQuery({
    queryKey: ["ask-ytm", isins.join(",")],
    queryFn: () => api.kite.askYtm(isins),
    enabled: kiteConnected && isins.length > 0,
    staleTime: 55_000, // just under the server's 60s per-user cache
    refetchOnWindowFocus: false,
  });
  const [snapshot] = useState(loadAskSnapshot);
  useEffect(() => {
    if (askYtm.data?.items?.length) saveAskSnapshot(askYtm.data.items);
  }, [askYtm.data]);
  const askMap = useMemo(() => {
    // Fresh data wins outright (even empty ⇒ the ytm fallback below);
    // the snapshot only bridges the seconds before the first fetch lands.
    if (askYtm.data) {
      const m = new Map<string, AskYtm>();
      for (const item of askYtm.data.items) m.set(item.isin, item);
      return m;
    }
    return snapshot ?? new Map<string, AskYtm>();
  }, [askYtm.data, snapshot]);

  // Painting before the asks arrive would order by LTP-YTM and then
  // visibly reorder — hold the loading state for the one first fetch
  // (kite status unknown counts as pending; a disconnected user's ask
  // query is disabled, so isLoading stays false and nothing holds).
  const holdForAsks =
    sort.key === "ask_ytm" &&
    snapshot == null &&
    bonds.length > 0 &&
    (kite === undefined || (kiteConnected && askYtm.isLoading));

  const cols = kiteConnected ? KITE_COLS : BASE_COLS;
  const { widths, tableRef, startResize } = useColumnWidths(COLUMN_WIDTHS_KEY, cols.length, {
    legacyKey: LEGACY_COLUMN_WIDTHS_KEY,
  });

  // Ask-YTM sorting uses only that column's values (no-ask rows last).
  // Only when the column has no data at all — Kite disconnected, or the
  // market closed so nothing quotes — does the sort fall back to the
  // LTP-based YTM column wholesale.
  const askAvailable = useMemo(() => [...askMap.values()].some((a) => a.ytm != null), [askMap]);
  const effectiveKey = sort.key === "ask_ytm" && !askAvailable ? "ytm" : sort.key;

  const sorted = useMemo(() => {
    // Unreliable YTMs (near-maturity artifacts, OTC disagreement) sort
    // as missing — junk numbers must never top the list.
    const get = (b: Bond) =>
      effectiveKey === "ask_ytm"
        ? (askMap.get(b.isin)?.ytm ?? null)
        : effectiveKey === "ytm"
          ? b.ytm_reliable === false
            ? null
            : b.ytm
          : b[effectiveKey];
    const rows = [...bonds];
    rows.sort((a, b) => {
      const av = get(a);
      const bv = get(b);
      if (av == null) return 1;
      if (bv == null) return -1;
      return (av < bv ? -1 : av > bv ? 1 : 0) * sort.dir;
    });
    return rows;
  }, [bonds, sort.dir, effectiveKey, askMap]);

  // The arrow sits on the column that actually orders the rows.
  const shownSortKey = effectiveKey;

  if (error) {
    return <div className="p-8 text-center text-red-400 text-sm">{error}</div>;
  }
  if (loading || holdForAsks) {
    return (
      <div className="p-8 text-center text-zinc-500 text-sm animate-pulse">
        {loading ? "Loading bonds…" : "Loading live asks…"}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto scroll-slim">
      <table
        ref={tableRef}
        className={`w-full text-sm ${widths && widths.length === cols.length ? "table-fixed" : ""}`}
      >
        {widths && widths.length === cols.length && (
          <colgroup>
            {widths.map((w, i) => (
              <col key={i} style={{ width: w }} />
            ))}
          </colgroup>
        )}
        <thead className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500 border-b-2 border-zinc-100">
          <tr>
            {cols.map((c, i) => (
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
                <span className={c.key === shownSortKey ? "text-zinc-200" : undefined}>
                  {c.label}
                </span>
                {c.key === shownSortKey && (
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
            <Row
              key={b.isin}
              bond={b}
              onSelect={onSelect}
              ask={kiteConnected ? (askMap.get(b.isin) ?? null) : undefined}
            />
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
  ask,
}: {
  bond: Bond;
  onSelect: (isin: string, tab?: "ratings" | "chart" | "order book") => void;
  /** undefined = Kite not connected (no column); null = no live ask. */
  ask?: AskYtm | null;
}) {
  return (
    <tr
      onClick={() => onSelect(b.isin)}
      className="group/row border-b border-zinc-900 hover:bg-zinc-900/60 cursor-pointer transition-colors"
    >
      <td className="pl-3 pr-1 py-2.5 w-14">
        <span className="flex items-center gap-1.5">
          <HoldMark isin={b.isin} />
          <WatchStar isin={b.isin} />
        </span>
      </td>
      <td className="px-3 py-2.5 w-80 max-w-80">
        <div className="flex items-center gap-1.5">
          <span className="font-medium text-zinc-200 hover:text-emerald-400 hover:underline underline-offset-2 transition-colors">
            {b.nse_symbol ?? b.bse_scrip_id ?? b.isin}
          </span>
          <span className="flex items-center gap-1.5 opacity-0 group-hover/row:opacity-100 transition-opacity">
            <CopyBtn text={b.nse_symbol ?? b.bse_scrip_id ?? b.isin} label="symbol" />
            <CopyBtn text={b.isin} label="ISIN" chip="isin" />
          </span>
          {b.in_default && (
            <span
              title="Issuer has missed payments on this bond"
              className="shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-red-500/15 text-red-400 border border-red-500/30"
            >
              DEFAULT
            </span>
          )}
        </div>
        <div className="text-[11px] text-zinc-500 truncate max-w-76">{b.issuer_name ?? b.isin}</div>
      </td>
      {/* Merged column: YTM (at the last traded price) on top, the LTP
          it was computed from as the sub-line. */}
      <td className="px-3 py-2.5 whitespace-nowrap">
        <div>
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
                  : "Unreliable — near maturity, out of band, or disagrees with recent OTC prints; treat with caution"
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
            <span
              className="ml-1 text-[10px] text-zinc-500"
              title="Floating / market-linked coupon"
            >
              FLT
            </span>
          )}
        </div>
        {b.ltp != null && (
          <div
            className={`group/ltp text-[11px] font-normal ${
              b.stale && (b.days_since_traded ?? 0) > 90 ? "text-amber-500/80" : "text-zinc-500"
            }`}
            onClick={(e) => {
              e.stopPropagation();
              onSelect(b.isin, "chart");
            }}
            title={
              [
                b.stale && b.days_since_traded != null
                  ? `No trades seen for ${b.days_since_traded} days — this price and its YTM may be outdated`
                  : null,
                b.face_value != null && b.ltp < b.face_value ? "Below face value" : null,
              ]
                .filter(Boolean)
                .join("\n") || undefined
            }
          >
            <span className="group-hover/ltp:underline decoration-zinc-600 underline-offset-2 cursor-pointer">
              {fmt.inrFull(b.ltp)}
            </span>
            {!b.stale && b.prev_ltp != null && b.ltp !== b.prev_ltp && (
              <span
                className={`ml-1 text-[10px] ${
                  b.ltp > b.prev_ltp ? "text-emerald-500/80" : "text-red-400/70"
                }`}
                title={`LTP on previous trading day: ${fmt.inrFull(b.prev_ltp)}`}
              >
                {b.ltp > b.prev_ltp ? "▲" : "▼"}
              </span>
            )}
            {b.stale && <span className="ml-1 text-[10px] text-zinc-600 align-middle">⚠</span>}
          </div>
        )}
      </td>
      {/* YTM @ Ask (only when Kite is connected): the yield locked buying
          at the best resting ask across NSE/BSE, from the user's session.
          Clicking jumps to the order book to act on it. */}
      {ask !== undefined && (
        <td
          className="group/ask px-3 py-2.5 whitespace-nowrap cursor-pointer"
          title={
            "Yield locked buying at the best ask (NSE/BSE), from your Kite session" +
            (ask?.ask_price != null && ask?.ask_qty != null
              ? ` — ticket ${fmt.inr(ask.ask_price * ask.ask_qty)} to lift the best ask`
              : "")
          }
          onClick={(e) => {
            e.stopPropagation();
            onSelect(b.isin, "order book");
          }}
        >
          {ask == null ? (
            <span className="text-zinc-600">–</span>
          ) : (
            <>
              <div
                className={`${ask.ytm != null ? "text-zinc-200 font-medium" : "text-zinc-500"} group-hover/ask:underline decoration-zinc-600 underline-offset-2`}
              >
                {ask.ytm != null ? fmt.pct(ask.ytm) : "–"}
              </div>
              <div className="text-[11px] text-zinc-500">
                {fmt.inrFull(ask.ask_price)}
                {ask.ask_qty != null && ` × ${fmt.num(ask.ask_qty, 0)}`}
              </div>
            </>
          )}
        </td>
      )}
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
        {b.otc_turnover != null && (
          <div
            className="text-[11px] text-zinc-500"
            title={
              "OTC turnover on its last OTC trade date" +
              (b.otc_price_pct != null ? ` · at ${fmt.num(Number(b.otc_price_pct))}% of face` : "")
            }
          >
            OTC {fmt.inr(b.otc_turnover)}
          </div>
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
            <div className="text-zinc-200 font-medium">
              {b.years_to_maturity != null
                ? yearsMonths(b.years_to_maturity)
                : monthYear(b.maturity_date)}
            </div>
            {b.years_to_maturity != null && (
              <div className="text-[11px] text-zinc-500">{monthYear(b.maturity_date)}</div>
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
  const popoverRef = useRef<HTMLSpanElement>(null);
  const watched = starred(isin);

  useEffect(() => {
    if (!pos) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (!wrapRef.current?.contains(target) && !popoverRef.current?.contains(target)) setPos(null);
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
      {pos &&
        createPortal(
          <span
            ref={popoverRef}
            className="fixed z-50 w-56 py-1 rounded-md border border-zinc-700 bg-zinc-900 shadow-xl text-left"
            style={{ left: pos.x, top: pos.y }}
            onClick={(e) => e.stopPropagation()}
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
          </span>,
          document.body,
        )}
    </span>
  );
}

/** Bookmark shape — filled while held, outline otherwise. */
export function BookmarkIcon({ filled }: { filled: boolean }) {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true" className="block">
      <path
        d="M4 1.5h8a1 1 0 0 1 1 1v12l-5-3.6-5 3.6v-12a1 1 0 0 1 1-1z"
        fill={filled ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Bookmark opens the add-lot form; filled while the bond is in the portfolio. */
export function HoldMark({ isin }: { isin: string }) {
  const { has } = useHeldIsins();
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const held = has(isin);
  return (
    <span className="relative" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        onClick={(e) => {
          if (pos) {
            setPos(null);
            return;
          }
          const r = e.currentTarget.getBoundingClientRect();
          setPos({
            x: Math.max(8, Math.min(r.left, window.innerWidth - 248)),
            y: r.bottom + 6,
          });
        }}
        className={`leading-none ${held ? "text-amber-400" : "text-zinc-700 hover:text-zinc-400"}`}
        title={held ? "In your portfolio — add another lot" : "Add to portfolio"}
      >
        <BookmarkIcon filled={held} />
      </button>
      {pos &&
        createPortal(
          <>
            <span
              className="fixed inset-0 z-40 block"
              onClick={() => setPos(null)}
              aria-hidden="true"
            />
            <AddLotPopover isin={isin} held={held} pos={pos} onClose={() => setPos(null)} />
          </>,
          document.body,
        )}
    </span>
  );
}
