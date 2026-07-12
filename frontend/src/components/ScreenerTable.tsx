import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, fmt } from "../api/client";
import type { Bond, BondsResponse } from "../api/types";
import CopyBtn from "./CopyBtn";
import RatingBadge from "./RatingBadge";

const monthYear = (d: string | null) =>
  d ? new Date(d).toLocaleDateString("en-IN", { month: "short", year: "numeric" }) : null;

type SortKey = keyof Pick<
  Bond,
  "ytm" | "ltp" | "turnover" | "years_to_maturity" | "rating_rank" | "days_since_traded"
>;

const COLS: { key: SortKey | null; label: string; right?: boolean }[] = [
  { key: null, label: "★" },
  { key: null, label: "Symbol" },
  { key: "ytm", label: "YTM %" },
  { key: "ltp", label: "LTP" },
  { key: "turnover", label: "Turnover" },
  { key: "rating_rank", label: "Rating" },
  { key: "years_to_maturity", label: "Maturity", right: true },
  { key: "days_since_traded", label: "Traded", right: true },
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
      <table className="w-full text-sm">
        <thead className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500 border-b-2 border-zinc-100">
          <tr>
            {COLS.map((c, i) => (
              <th
                key={i}
                className={`px-3 py-3 whitespace-nowrap ${c.right ? "text-right" : "text-left"} ${c.key ? "cursor-pointer select-none hover:text-zinc-300" : ""}`}
                onClick={() =>
                  c.key &&
                  setSort((s) => ({
                    key: c.key!,
                    dir: s.key === c.key ? ((s.dir * -1) as 1 | -1) : -1,
                  }))
                }
              >
                {c.label}
                {c.key === sort.key && (
                  <span className="ml-1 text-emerald-400">{sort.dir === -1 ? "▼" : "▲"}</span>
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
      className="border-b border-zinc-900 hover:bg-zinc-900/60 cursor-pointer transition-colors"
    >
      <td className="pl-3 pr-1 py-2.5 w-8">
        <WatchStar isin={b.isin} watched={b.in_watchlist} />
      </td>
      <td className="px-3 py-2.5 min-w-52">
        <div className="flex items-center gap-1.5">
          <span className="font-semibold text-zinc-100 underline decoration-zinc-700 underline-offset-2">
            {b.nse_symbol ?? b.bse_scrip_id ?? b.isin}
          </span>
          <CopyBtn text={b.nse_symbol ?? b.bse_scrip_id ?? b.isin} label="symbol" />
          {b.in_default && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/15 text-red-400 border border-red-500/30">
              DEFAULT
            </span>
          )}
          {b.rating_change === "down" && (
            <span className="text-red-400 text-xs" title={`was ${b.rating_prev}`}>
              ▼
            </span>
          )}
          {b.rating_change === "up" && (
            <span className="text-emerald-400 text-xs" title={`was ${b.rating_prev}`}>
              ▲
            </span>
          )}
        </div>
        <div className="text-[11px] text-zinc-500 truncate max-w-64">{b.issuer_name ?? b.isin}</div>
      </td>
      <td className="px-3 py-2.5 whitespace-nowrap">
        <span
          className={
            b.ytm_reliable === false
              ? "text-zinc-600 font-bold"
              : b.ytm != null && b.ytm >= 10
                ? "text-emerald-400 font-bold"
                : b.ytm != null
                  ? "text-zinc-100 font-bold"
                  : "text-zinc-500"
          }
          title={
            b.ytm_reliable === false
              ? "Likely an annualization artifact (near maturity or out of band) — treat with caution"
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
        {b.coupon != null && (
          <div className="text-[11px] text-zinc-500" title="Coupon rate">
            {fmt.pct(b.coupon)} cpn
          </div>
        )}
      </td>
      <td
        className={`px-3 py-2.5 whitespace-nowrap ${
          b.stale && (b.days_since_traded ?? 0) > 90 ? "text-amber-500/80" : "text-zinc-200"
        }`}
        title={[
          b.stale && b.days_since_traded != null
            ? `No trades seen for ${b.days_since_traded} days — this price and its YTM may be outdated`
            : null,
          b.face_value != null && b.ltp != null && b.ltp < b.face_value ? "Below face value" : null,
        ]
          .filter(Boolean)
          .join("\n")}
      >
        <div>
          {fmt.inr(b.ltp)}
          {b.stale && b.ltp != null && (
            <span className="ml-1 text-[10px] text-zinc-600 align-middle">⚠</span>
          )}
        </div>
        {b.face_value != null && (
          <div className="text-[11px] text-zinc-500">FV {fmt.inr(b.face_value)}</div>
        )}
      </td>
      <td
        className="px-3 py-2.5 whitespace-nowrap text-sm leading-snug hover:bg-zinc-900"
        onClick={(e) => {
          e.stopPropagation();
          onSelect(b.isin, "chart");
        }}
        title="Open price chart"
      >
        {b.nse_turnover == null && b.bse_turnover == null && b.otc_turnover == null ? (
          <span className="text-zinc-500">–</span>
        ) : (
          <>
            {(b.nse_turnover != null || b.bse_turnover != null) && (
              <div>
                <span className="text-zinc-200">
                  {fmt.inr((b.nse_turnover ?? 0) + (b.bse_turnover ?? 0))}
                </span>
                <span className="ml-1.5 text-[10px] uppercase tracking-wide text-zinc-500">
                  Retail
                </span>
              </div>
            )}
            {b.otc_turnover != null && (
              <div>
                <span className="text-zinc-200">{fmt.inr(b.otc_turnover)}</span>
                <span className="ml-1.5 text-[10px] uppercase tracking-wide text-zinc-500">
                  OTC
                </span>
              </div>
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
        title="Open rating history"
      >
        <RatingBadge rating={b.credit_rating} />
      </td>
      <td className="px-3 py-2.5 whitespace-nowrap text-right">
        {b.maturity_date != null ? (
          <>
            <div className="text-zinc-200">{monthYear(b.maturity_date)}</div>
            {b.years_to_maturity != null && (
              <div className="text-[11px] text-zinc-500">{b.years_to_maturity.toFixed(1)} yr</div>
            )}
          </>
        ) : b.perpetual ? (
          <span className="text-zinc-500">perp</span>
        ) : (
          <span className="text-zinc-500">–</span>
        )}
      </td>
      <td className="px-3 py-2.5 whitespace-nowrap text-right">
        <span className={b.stale ? "text-zinc-600" : "text-zinc-400"}>
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

export function WatchStar({ isin, watched }: { isin: string; watched: boolean }) {
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: () => (watched ? api.unwatch(isin) : api.watch(isin)),
    onSuccess: () => {
      // Patch every cached screener result instead of refetching.
      qc.setQueriesData<BondsResponse>({ queryKey: ["bonds"] }, (old) =>
        old
          ? {
              ...old,
              bonds: old.bonds.map((b) => (b.isin === isin ? { ...b, in_watchlist: !watched } : b)),
            }
          : old,
      );
      qc.invalidateQueries({ queryKey: ["bond", isin] });
    },
  });
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        toggle.mutate();
      }}
      className={`text-base leading-none ${watched ? "text-amber-400" : "text-zinc-700 hover:text-zinc-400"}`}
      title={watched ? "Remove from watchlist" : "Add to watchlist"}
    >
      {watched ? "★" : "☆"}
    </button>
  );
}
