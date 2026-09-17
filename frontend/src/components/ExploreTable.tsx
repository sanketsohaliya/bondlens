import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, fmt } from "../api/client";
import type { AskYtm, Bond, YieldValuation } from "../api/types";
import { useAuth } from "../lib/auth";
import {
  absReturnText,
  askTicketText,
  otcLine,
  paymentsLeftText,
  payoutDisplay,
  shouldOpenRow,
  usePayoutDay,
} from "../lib/exploreAmounts";
import { LINK_TIPS, nsdlUrl } from "../lib/links";
import { nextListName, useHeldIsins, useWatchlists } from "../lib/lists";
import {
  fmtIstTime,
  hasYieldReview,
  useQuoteClock,
  valuationIsCurrent,
  valuationTooltip,
} from "../lib/quoteValuation";
import { useAskCapture } from "../lib/useAskCapture";
import { useColumnWidths } from "../lib/useColumnWidths";
import AddLotPopover from "./AddLotPopover";
import AiPromptBtn from "./AiPromptBtn";
import CopyText from "./CopyText";
import InlineMetadata, { MetadataSeparator } from "./InlineMetadata";
import RatingBadge from "./RatingBadge";
import YieldQuote from "./YieldQuote";

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

const isZeroCoupon = (b: Pick<Bond, "coupon_basis" | "schedule_shape">) =>
  (b.coupon_basis ?? "").toLowerCase().includes("zero") || b.schedule_shape === "zero_coupon";

/** Payout structure — the column's primary line. */
const payoutStructure = (b: Pick<Bond, "schedule_shape" | "frequency">): string | null => {
  switch (b.schedule_shape) {
    case "staggered":
      return "Staggered";
    case "cumulative":
    case "zero_coupon":
      return "Pays at maturity";
    case "perpetual":
      return "Perpetual";
    case "irregular":
      return "Irregular";
    case "plain":
      return "Bullet";
    default:
      return b.frequency === "cumulative" ? "Pays at maturity" : null;
  }
};

type PayoutBond = Pick<
  Bond,
  | "schedule_shape"
  | "coupon_basis"
  | "frequency"
  | "coupon"
  | "future_flows"
  | "total_flows"
  | "payout_total_per_unit"
  | "payout_total_state"
  | "payout_as_of"
  | "payout_valid_until"
>;

/** Total return to maturity, muted, leading the second line of both yield
 * cells. Deliberately NOT inside the price's click target or its stale-amber
 * colouring: the figure is about the return, not the price. */
function AbsReturn({ value }: { value: YieldValuation | AskYtm | null | undefined }) {
  const abs = absReturnText(value);
  return abs ? (
    <span className="shrink-0 text-[11px] text-zinc-500" title={abs.title}>
      {abs.text}
    </span>
  ) : null;
}

/** Hover-revealed external link in the symbol cell. Stops propagation so the
 * row's own click (open drawer) does not fire. */
export function RowLink({ href, label, tip }: { href: string; label: string; tip: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={tip}
      aria-label={tip}
      onClick={(e) => e.stopPropagation()}
      className="text-[10px] whitespace-nowrap text-zinc-600 hover:text-emerald-400 transition"
    >
      {label} ↗
    </a>
  );
}

export function PayoutCell({
  bond: b,
  today,
  onOpen,
  payout = payoutDisplay(b, today),
}: {
  bond: PayoutBond;
  today: string;
  onOpen: () => void;
  payout?: ReturnType<typeof payoutDisplay>;
}) {
  return (
    <button
      type="button"
      className="block w-full min-w-0 overflow-hidden text-left"
      title={payout.detail}
      aria-label={`Open Cashflows. ${payout.detail.replace(/\n/g, " ")}`}
      onClick={(e) => {
        e.stopPropagation();
        onOpen();
      }}
    >
      <div className="flex min-w-0 items-baseline gap-2 overflow-hidden text-zinc-200 group-hover/coupon:underline decoration-zinc-600 underline-offset-2">
        <span className="truncate">{payoutStructure(b) ?? "–"}</span>
        {payout.label && (
          <span className="shrink-0 max-w-full truncate text-[11px] text-zinc-400">
            {payout.label}
          </span>
        )}
      </div>
      <div className="truncate text-[11px] text-zinc-500">
        {isZeroCoupon(b) ? (
          "Zero coupon"
        ) : b.coupon != null ? (
          <>
            {fmt.num(Number(b.coupon))}%
            {b.coupon_basis?.startsWith("Variable") && (
              <span
                className="ml-1 text-[10px] px-1 rounded bg-amber-500/10 text-amber-500/90"
                title="Floating / market-linked coupon — the rate resets"
              >
                float
              </span>
            )}
            {b.frequency && b.frequency !== "cumulative" && ` ${b.frequency}`}
          </>
        ) : null}
        {payout.showCount &&
          b.future_flows != null &&
          b.total_flows != null &&
          b.total_flows > 0 && (
            <span
              title={`${paymentsLeftText(b.future_flows, b.total_flows)} (interest and principal payments a buyer today would receive)`}
            >
              {(isZeroCoupon(b) || b.coupon != null) && <MetadataSeparator />}
              {b.future_flows}/{b.total_flows} left
            </span>
          )}
      </div>
    </button>
  );
}

export function AskQuoteCell({ ask, onOpen }: { ask: AskYtm | null; onOpen: () => void }) {
  const ticket = askTicketText(ask);
  // The ask is a snapshot from page load; only a size-less level is
  // withheld. Its capture time lives in the tooltip.
  const stale = ask != null && !(ask.ask_qty && ask.ask_qty > 0);
  const captured = stale ? null : askCapturedLabel(ask);
  const detail = `${ticket.detail}\n${valuationTooltip(ask)}${
    captured
      ? `\nBest ask ${captured} IST; captured when the page loaded - reload for a fresh book.`
      : ""
  }`;
  return (
    <button
      type="button"
      className="block w-full min-w-0 overflow-hidden text-left"
      title={detail}
      aria-label={`Open Order Book. ${detail}`}
      onClick={(e) => {
        e.stopPropagation();
        onOpen();
      }}
    >
      <div
        className={`truncate ${ask?.ytm != null ? "text-zinc-200 font-medium" : "text-zinc-500"} group-hover/ask:underline decoration-zinc-600 underline-offset-2`}
      >
        <YieldQuote value={ask} stale={stale} />
      </div>
      {ask && (
        <div className="flex items-center truncate text-[11px]">
          <InlineMetadata
            parts={[
              !stale && absReturnText(ask) && <AbsReturn key="return" value={ask} />,
              <span key="ticket" className="truncate text-zinc-400">
                {ticket.label}
              </span>,
            ]}
          />
        </div>
      )}
    </button>
  );
}

type SortKey =
  | keyof Pick<Bond, "ytm" | "turnover" | "years_to_maturity" | "rating_rank" | "coupon">
  | "ask_ytm"; // client-side only — computed from the user's Kite session

const SORT_KEYS: ReadonlySet<string> = new Set([
  "ytm",
  "turnover",
  "years_to_maturity",
  "rating_rank",
  "coupon",
  "ask_ytm",
]);

type Col = { key: SortKey | null; label: string; right?: boolean };

// Trade recency lives beside the price ("₹955 ▼ | 4d ago"): it qualifies
// the LTP rather than standing on its own, and it freed a column.
const BASE_COLS: Col[] = [
  { key: null, label: "" },
  { key: null, label: "Symbol" },
  { key: "ytm", label: "YTM @ LTP" },
  { key: "turnover", label: "Retail turnover" },
  { key: "rating_rank", label: "Rating" },
  { key: "coupon", label: "Payout" },
  { key: "years_to_maturity", label: "Maturity", right: true },
];

/** "today" / "4d ago" for the LTP's age. */
export function tradedAgo(days: number): string {
  return days <= 1 ? "today" : `${days}d ago`;
}

/** With Kite connected, YTM @ Ask slots in right after YTM @ LTP. */
const KITE_COLS: Col[] = [
  ...BASE_COLS.slice(0, 3),
  { key: "ask_ytm", label: "YTM @ Ask" },
  ...BASE_COLS.slice(3),
];

const COLUMN_WIDTHS_KEY = "explore-col-widths";
const PAGE_ROWS = 100;
const LEGACY_COLUMN_WIDTHS_KEY = "screener-col-widths";

/** "as of 10:42" - when the best ask was captured (tooltip only). */
export function askCapturedLabel(ask: AskYtm | null | undefined): string | null {
  if (!ask) return null;
  const hm = fmtIstTime(ask.fetched_at);
  return hm ? `as of ${hm}` : "time unknown";
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
  onSelect: (isin: string, tab?: "cashflows" | "ratings" | "chart" | "order book") => void;
  initialSort?: SortState;
  onSortChange?: (s: SortState) => void;
}) {
  const today = usePayoutDay();
  const now = useQuoteClock();
  const { user } = useAuth();
  // A saved sort may name a column that no longer exists (Last traded
  // moved into the LTP cell); fall back rather than sort by nothing.
  const [sort, setSortState] = useState<SortState>(
    initialSort && SORT_KEYS.has(initialSort.key) ? initialSort : { key: "ask_ytm", dir: -1 },
  );
  const setSort = (update: (s: SortState) => SortState) =>
    setSortState((s) => {
      const next = update(s);
      onSortChange?.(next);
      return next;
    });

  // YTM @ Ask: best-ask yields under the user's own Kite session,
  // captured for the WHOLE universe once per page load (filters only
  // narrow that set, so no bond with an ask is ever missed).
  const { data: kite } = useQuery({
    queryKey: ["kite-status"],
    queryFn: api.kite.status,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const kiteConnected = kite?.connected ?? false;
  const capture = useAskCapture(kiteConnected, user.id, today);
  const askMap = useMemo(
    () =>
      new Map(
        [...capture.asks.values()]
          .filter((item) => valuationIsCurrent(item, now))
          .map((item) => [item.isin, item]),
      ),
    [capture.asks, now],
  );

  // Painting before any asks arrive would order by LTP-YTM and then
  // visibly reorder — hold the loading state until the first page lands
  // (kite status unknown counts as pending; a disconnected user's capture
  // is disabled, so nothing holds). Later pages fill in place; the sort
  // settles as they land, once per load.
  const holdForAsks =
    sort.key === "ask_ytm" &&
    bonds.length > 0 &&
    (kite === undefined || (kiteConnected && capture.loading && capture.done === 0));

  const cols = kiteConnected ? KITE_COLS : BASE_COLS;
  const { widths, tableRef, startResize, tableProps } = useColumnWidths(
    COLUMN_WIDTHS_KEY,
    cols.length,
    {
      legacyKey: LEGACY_COLUMN_WIDTHS_KEY,
    },
  );

  // Ask-YTM sorting uses only that column's values (no-ask rows last).
  // Only when the column has no data at all — Kite disconnected, or the
  // market closed so nothing quotes — does the sort fall back to the
  // LTP-based YTM column wholesale.
  const askAvailable = useMemo(() => [...askMap.values()].some((a) => a.ytm != null), [askMap]);
  const effectiveKey = sort.key === "ask_ytm" && !askAvailable ? "ytm" : sort.key;

  const sorted = useMemo(() => {
    const get = (b: Bond) =>
      effectiveKey === "ask_ytm" ? (askMap.get(b.isin)?.ytm ?? null) : b[effectiveKey];
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

  // Sorting always sees the whole universe; only the RENDER is windowed,
  // 100 rows at a time, growing as the reader nears the bottom (thousands
  // of DOM rows jank the browser and drown the eye). A new bond set or a
  // new ordering starts again from its top 100.
  const [shownCount, setShownCount] = useState(PAGE_ROWS);
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset on a new set/order is the point
  useEffect(() => setShownCount(PAGE_ROWS), [bonds, effectiveKey, sort.dir]);
  const visibleRows = sorted.slice(0, shownCount);
  const exhausted = shownCount >= sorted.length;
  const sentinelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || exhausted || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting))
          setShownCount((n) => Math.min(n + PAGE_ROWS, sorted.length));
      },
      { rootMargin: "1200px 0px" }, // start the next 100 about two screens early
    );
    io.observe(el);
    return () => io.disconnect();
  }, [exhausted, sorted.length]);

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
      {kiteConnected && capture.error && (
        <div className="p-3 text-xs text-amber-400">
          Ask capture stopped after {capture.done}
          {capture.pages ? ` of ${capture.pages}` : ""} page(s); some bonds may show no ask.{" "}
          <button type="button" onClick={capture.retry} className="underline">
            Retry
          </button>
        </div>
      )}
      <table ref={tableRef} className={`text-sm ${tableProps.className}`} style={tableProps.style}>
        {widths && (
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
                {c.key === "ask_ytm" && capture.loading && (
                  <span
                    className="ml-2 normal-case tracking-normal font-normal text-zinc-600 animate-pulse"
                    title="Capturing the best ask for every listed bond; filters apply to the full set once done."
                  >
                    loading {capture.done}
                    {capture.pages ? `/${capture.pages}` : ""}
                  </span>
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
          {visibleRows.map((b) => (
            <Row
              key={b.isin}
              bond={b}
              today={today}
              onSelect={onSelect}
              ask={kiteConnected ? (askMap.get(b.isin) ?? null) : undefined}
            />
          ))}
        </tbody>
      </table>
      <div ref={sentinelRef} className="h-px" aria-hidden="true" />
      {sorted.length > 0 && (
        <div className="flex items-center justify-center gap-3 py-3 text-xs text-zinc-500">
          <span>
            showing {Math.min(shownCount, sorted.length).toLocaleString("en-IN")} of{" "}
            {sorted.length.toLocaleString("en-IN")}
          </span>
          {!exhausted && (
            <button
              type="button"
              onClick={() => setShownCount((n) => Math.min(n + PAGE_ROWS, sorted.length))}
              className="font-medium text-emerald-400 hover:text-emerald-300"
            >
              Show more
            </button>
          )}
        </div>
      )}
      {sorted.length === 0 && (
        <div className="p-8 text-center text-zinc-500 text-sm">No bonds match the filters.</div>
      )}
    </div>
  );
}

export function Row({
  bond: b,
  today,
  onSelect,
  ask,
}: {
  bond: Bond;
  today: string;
  onSelect: (isin: string, tab?: "cashflows" | "ratings" | "chart" | "order book") => void;
  /** undefined = Kite not connected (no column); null = no live ask. */
  ask?: AskYtm | null;
}) {
  const payout = payoutDisplay(b, today);
  const abs = b.ytm != null ? absReturnText(b.valuation) : null;
  return (
    <tr
      onClick={() => {
        if (!shouldOpenRow(window.getSelection()?.toString())) return;
        onSelect(b.isin);
      }}
      className="group/row border-b border-zinc-900 hover:bg-zinc-900/60 cursor-pointer transition-colors"
    >
      <td className="pl-3 pr-1 py-2.5 w-14">
        <span className="flex items-center gap-1.5">
          <HoldMark isin={b.isin} />
          <WatchStar isin={b.isin} />
        </span>
      </td>
      <td className="px-3 py-2.5 w-80 max-w-80">
        {/* Issuer leads: it is what you scan for. Symbol, ISIN and the
            hover-revealed actions sit beneath, separated by spacing only. */}
        <div className="flex items-center gap-1.5 min-w-0">
          <span
            className="font-medium text-zinc-200 truncate hover:text-emerald-400 hover:underline underline-offset-2 transition-colors"
            title={b.issuer_name ?? undefined}
          >
            {b.issuer_name ?? b.isin}
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
        <div className="flex items-center text-[11px] min-w-0">
          <InlineMetadata
            parts={[
              <CopyText
                key="symbol"
                value={b.nse_symbol ?? b.bse_scrip_id ?? b.isin}
                label="symbol"
                className="text-zinc-500 shrink-0"
              />,
              <CopyText key="isin" value={b.isin} label="ISIN" className="text-zinc-500" />,
            ]}
          />
          <span className="flex items-center shrink-0 opacity-0 group-hover/row:opacity-100 transition-opacity">
            <MetadataSeparator />
            <InlineMetadata
              parts={[
                <RowLink key="nsdl" href={nsdlUrl(b.isin)} label="NSDL" tip={LINK_TIPS.nsdl} />,
                b.kite_url && (
                  <RowLink key="kite" href={b.kite_url} label="Kite" tip={LINK_TIPS.kite} />
                ),
                <AiPromptBtn key="ai" isin={b.isin} variant="row" />,
              ]}
            />
          </span>
        </div>
      </td>
      {/* Merged column: YTM (at the last traded price) on top, the LTP
          it was computed from as the sub-line. */}
      <td className="px-3 py-2.5 whitespace-nowrap">
        <div>
          <span
            className={b.ytm != null ? "text-zinc-200 font-medium" : "text-zinc-500"}
            title={valuationTooltip(b.valuation)}
          >
            {fmt.pct(b.ytm)}
          </span>
          {b.ytm != null && hasYieldReview(b.valuation, b.ytm_reliable) && (
            <span
              className="ml-1 text-[10px] text-amber-500/80 align-middle"
              title={valuationTooltip(b.valuation)}
            >
              ⚠
            </span>
          )}
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
        {(b.ltp != null || abs) && (
          <div className="flex items-center text-[11px] font-normal">
            {abs && <AbsReturn value={b.valuation} />}
            {abs && b.ltp != null && <MetadataSeparator />}
            {b.ltp != null && (
              <div
                className={`group/ltp ${
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
                {b.days_since_traded != null && (
                  <>
                    <MetadataSeparator />
                    <span>{tradedAgo(b.days_since_traded)}</span>
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </td>
      {/* YTM @ Ask (only when Kite is connected): indicative gross yield
          at a usable best-ask snapshot across NSE/BSE, from the user's session.
          Clicking jumps to the order book to act on it. */}
      {ask !== undefined && (
        <td
          className="group/ask px-3 py-2.5 whitespace-nowrap cursor-pointer"
          title={askTicketText(ask).detail}
          onClick={(e) => {
            e.stopPropagation();
            onSelect(b.isin, "order book");
          }}
        >
          <AskQuoteCell ask={ask} onOpen={() => onSelect(b.isin, "order book")} />
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
        {(() => {
          const otc = otcLine(b);
          return otc ? (
            <div className="text-[11px] text-zinc-500" title={otc.title}>
              {otc.text}
            </div>
          ) : null;
        })()}
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
          agencySuffix={
            b.secured != null && (
              <span
                className={b.secured ? "text-zinc-600" : "text-amber-500/50"}
                title={
                  b.secured
                    ? "Secured — backed by collateral / security"
                    : "Unsecured — no collateral backing; repayment relies on the issuer alone"
                }
              >
                {" "}
                ({b.secured ? "S" : "U"})
              </span>
            )
          }
        />
      </td>
      {/* Payout: structure primary, rate/frequency and remaining flow
          count muted beneath. Tooltip leads with the rupee total a buyer
          receives, then structure in plain words; click = the actual
          payment schedule. */}
      <td
        className="group/coupon px-3 py-2.5 whitespace-nowrap cursor-pointer"
        title={payout.detail}
        onClick={(e) => {
          e.stopPropagation();
          onSelect(b.isin, "cashflows");
        }}
      >
        <PayoutCell
          bond={b}
          today={today}
          payout={payout}
          onOpen={() => onSelect(b.isin, "cashflows")}
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
              <div className="flex items-center justify-end text-[11px] text-zinc-500">
                {monthYear(b.maturity_date)}
                {b.tenor_months != null && (
                  <>
                    <MetadataSeparator />
                    <span title="Original tenor per NSDL">{yearsMonths(b.tenor_months / 12)}</span>
                  </>
                )}
              </div>
            )}
          </>
        ) : b.perpetual ? (
          <span className="text-zinc-500">perp</span>
        ) : (
          <span className="text-zinc-500">–</span>
        )}
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
