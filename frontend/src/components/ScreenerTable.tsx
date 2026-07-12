import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, fmt } from "../api/client";
import type { Bond, BondsResponse } from "../api/types";
import RatingBadge from "./RatingBadge";

type SortKey = keyof Pick<
  Bond,
  | "ytm"
  | "ltp"
  | "turnover"
  | "years_to_maturity"
  | "rating_rank"
  | "coupon"
  | "current_yield"
  | "days_since_traded"
>;

const COLS: { key: SortKey | null; label: string; cls?: string }[] = [
  { key: null, label: "" },
  { key: null, label: "Bond" },
  { key: "ytm", label: "YTM" },
  { key: "current_yield", label: "CY" },
  { key: "ltp", label: "Price" },
  { key: "coupon", label: "Coupon" },
  { key: "rating_rank", label: "Rating" },
  { key: "years_to_maturity", label: "Matures" },
  { key: "turnover", label: "Turnover" },
  { key: "days_since_traded", label: "Traded" },
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
  onSelect: (isin: string) => void;
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
    <div className="overflow-x-auto scroll-slim rounded-lg border border-zinc-800">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-zinc-500 border-b border-zinc-800 bg-zinc-900/60">
            {COLS.map((c, i) => (
              <th
                key={i}
                className={`px-3 py-2.5 font-medium whitespace-nowrap ${c.key ? "cursor-pointer select-none hover:text-zinc-300" : ""}`}
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

function Row({ bond: b, onSelect }: { bond: Bond; onSelect: (isin: string) => void }) {
  return (
    <tr
      onClick={() => onSelect(b.isin)}
      className="border-b border-zinc-800/60 hover:bg-zinc-900/70 cursor-pointer transition-colors"
    >
      <td className="pl-3 pr-1 py-2.5 w-8">
        <WatchStar isin={b.isin} watched={b.in_watchlist} />
      </td>
      <td className="px-3 py-2.5 min-w-52">
        <div className="font-medium text-zinc-100 flex items-center gap-2">
          {b.nse_symbol ?? b.bse_scrip_id ?? b.isin}
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
        <div className="text-xs text-zinc-500 truncate max-w-64">{b.issuer_name ?? b.isin}</div>
      </td>
      <td className="px-3 py-2.5 whitespace-nowrap">
        <span
          className={
            b.ytm_reliable === false
              ? "text-zinc-600"
              : b.ytm != null && b.ytm >= 10
                ? "text-emerald-400 font-semibold"
                : "text-zinc-200"
          }
          title={
            b.ytm_reliable === false
              ? "Likely an annualization artifact (near maturity or out of band) — treat with caution"
              : undefined
          }
        >
          {fmt.pct(b.ytm)}
        </span>
        {b.ytm_method === "ytc" ? (
          <span
            className="ml-1 text-[10px] text-sky-500"
            title="Yield to earliest call (perpetual)"
          >
            YTC
          </span>
        ) : (
          b.ytm_method && (
            <span
              className={`ml-1 text-[10px] ${b.ytm_method === "exact" ? "text-emerald-600" : "text-zinc-600"}`}
              title={b.ytm_method === "exact" ? "From the actual payment schedule" : "Estimated"}
            >
              {b.ytm_method === "exact" ? "●" : "○"}
            </span>
          )
        )}
      </td>
      <td className="px-3 py-2.5 text-zinc-400 whitespace-nowrap">{fmt.pct(b.current_yield)}</td>
      <td className="px-3 py-2.5 whitespace-nowrap">
        <span className={b.stale ? "text-zinc-500" : "text-zinc-200"}>{fmt.inr(b.ltp)}</span>
        {b.face_value != null && b.ltp != null && b.ltp < b.face_value && (
          <span className="ml-1 text-[10px] text-sky-400" title="below face value">
            ↓par
          </span>
        )}
      </td>
      <td className="px-3 py-2.5 text-zinc-400 whitespace-nowrap">{fmt.pct(b.coupon)}</td>
      <td className="px-3 py-2.5 whitespace-nowrap">
        <RatingBadge rating={b.credit_rating} rank={b.rating_rank} />
      </td>
      <td className="px-3 py-2.5 text-zinc-400 whitespace-nowrap">
        {b.years_to_maturity != null
          ? `${b.years_to_maturity.toFixed(1)}y`
          : b.perpetual
            ? "perp"
            : "–"}
      </td>
      <td className="px-3 py-2.5 text-zinc-400 whitespace-nowrap">{fmt.inr(b.turnover)}</td>
      <td className="px-3 py-2.5 whitespace-nowrap">
        <span className={b.stale ? "text-amber-500" : "text-zinc-400"}>
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
