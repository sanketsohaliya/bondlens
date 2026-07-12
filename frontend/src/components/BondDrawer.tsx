import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, fmt } from "../api/client";
import type { BondDetail } from "../api/types";
import CashflowTable from "./CashflowTable";
import RatingBadge from "./RatingBadge";
import RatingTimeline from "./RatingTimeline";
import { WatchStar } from "./ScreenerTable";
import TradeChart from "./TradeChart";

type Tab = "overview" | "chart" | "cashflows" | "ratings";

export default function BondDrawer({ isin, onClose }: { isin: string; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("overview");
  const [whatIf, setWhatIf] = useState<string>("");
  const price = whatIf ? Number(whatIf) : undefined;

  const { data: bond, isLoading } = useQuery({
    queryKey: ["bond", isin, price],
    queryFn: () => api.bond(isin, price),
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <aside className="absolute right-0 top-0 h-full w-full max-w-3xl bg-zinc-950 border-l border-zinc-800 overflow-y-auto scroll-slim">
        {isLoading || !bond ? (
          <div className="p-8 text-zinc-500 animate-pulse">Loading {isin}…</div>
        ) : (
          <>
            <header className="sticky top-0 bg-zinc-950/95 backdrop-blur border-b border-zinc-800 p-4 z-10">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2.5">
                    <WatchStar isin={bond.isin} watched={bond.in_watchlist} />
                    <h2 className="text-lg font-bold">
                      {bond.nse_symbol ?? bond.bse_scrip_id ?? bond.isin}
                    </h2>
                    <RatingBadge rating={bond.credit_rating} rank={bond.rating_rank} />
                    {bond.in_default && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/15 text-red-400 border border-red-500/30">
                        DEFAULT
                      </span>
                    )}
                  </div>
                  <div className="text-sm text-zinc-400 mt-0.5">{bond.issuer_name}</div>
                  <div className="text-xs text-zinc-600 font-mono mt-0.5">
                    {bond.isin}
                    {bond.sector && ` · ${bond.sector}`}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {bond.kite_url && (
                    <a
                      href={bond.kite_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs px-3 py-1.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white font-medium"
                    >
                      Kite ↗
                    </a>
                  )}
                  <button
                    type="button"
                    onClick={onClose}
                    className="text-zinc-500 hover:text-zinc-200 text-xl leading-none px-2"
                  >
                    ×
                  </button>
                </div>
              </div>
              <nav className="flex gap-1 mt-3 -mb-px">
                {(["overview", "chart", "cashflows", "ratings"] as Tab[]).map((t) => (
                  <button
                    type="button"
                    key={t}
                    onClick={() => setTab(t)}
                    className={
                      "text-xs px-3 py-1.5 rounded-t-md border-b-2 capitalize transition-colors " +
                      (tab === t
                        ? "border-emerald-500 text-emerald-400"
                        : "border-transparent text-zinc-500 hover:text-zinc-300")
                    }
                  >
                    {t}
                  </button>
                ))}
              </nav>
            </header>

            <div className="p-4 space-y-4">
              {tab === "overview" && <Overview bond={bond} whatIf={whatIf} setWhatIf={setWhatIf} />}
              {tab === "chart" && <TradeChart isin={bond.isin} faceValue={bond.face_value} />}
              {tab === "cashflows" && <CashflowTable bond={bond} />}
              {tab === "ratings" && <RatingTimeline timeline={bond.rating_timeline} />}
            </div>
          </>
        )}
      </aside>
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 px-3 py-2.5">
      <div className="text-[11px] text-zinc-500">{label}</div>
      <div
        className={`text-sm font-semibold mt-0.5 ${accent ? "text-emerald-400" : "text-zinc-100"}`}
      >
        {value}
      </div>
    </div>
  );
}

function Overview({
  bond: b,
  whatIf,
  setWhatIf,
}: {
  bond: BondDetail;
  whatIf: string;
  setWhatIf: (v: string) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5">
        <Stat
          label={`YTM ${b.ytm_exact != null ? "(exact)" : "(approx)"}`}
          value={fmt.pct(b.ytm)}
          accent
        />
        {b.yield_to_worst != null && b.yield_to_worst !== b.ytm && (
          <Stat label="Yield to worst" value={fmt.pct(b.yield_to_worst)} />
        )}
        <Stat label="Current yield" value={fmt.pct(b.current_yield)} />
        <Stat label="Price (dirty)" value={fmt.inr(b.ltp)} />
        <Stat label="Clean price" value={fmt.inr(b.clean_price)} />
        <Stat label="Accrued" value={fmt.inr(b.accrued)} />
        <Stat label="Duration" value={b.duration != null ? `${b.duration}y` : "–"} />
        <Stat label="Coupon" value={`${fmt.pct(b.coupon)} ${b.frequency ?? ""}`} />
        <Stat label="Face value" value={fmt.inr(b.face_value)} />
        <Stat label="Matures" value={fmt.date(b.maturity_date)} />
        <Stat
          label="Last traded"
          value={b.trade_date ? `${fmt.date(b.trade_date)} (${b.exchange})` : "never"}
        />
        {b.otc_price_pct != null && (
          <Stat
            label="OTC (clean %)"
            value={`${fmt.num(b.otc_price_pct)}% @ ${fmt.pct(b.otc_yield)}`}
          />
        )}
      </div>

      <label className="flex items-center gap-3 text-xs text-zinc-400">
        What-if price:
        <input
          type="number"
          placeholder={b.ltp != null ? String(b.ltp) : "price ₹"}
          value={whatIf}
          onChange={(e) => setWhatIf(e.target.value)}
          className="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1.5 text-sm w-40 focus:outline-none focus:border-emerald-500"
        />
        {whatIf && (
          <button
            type="button"
            onClick={() => setWhatIf("")}
            className="text-red-400 hover:underline"
          >
            reset
          </button>
        )}
      </label>

      {b.schedule_warnings.length > 0 && (
        <div className="text-xs text-amber-500/90 bg-amber-500/5 border border-amber-500/20 rounded-lg p-3 space-y-1">
          {b.schedule_warnings.map((w, i) => (
            <div key={i}>⚠ {w}</div>
          ))}
        </div>
      )}

      {b.next_payment && (
        <div className="text-sm text-zinc-300">
          Next payment:{" "}
          <span className="font-semibold text-emerald-400">{fmt.inr(b.next_payment.amount)}</span>{" "}
          <span className="capitalize">({b.next_payment.event})</span> on{" "}
          {fmt.date(b.next_payment.due_date)} · total future{" "}
          <span className="font-semibold">{fmt.inr(b.future_total_per_unit)}</span>/unit
        </div>
      )}

      {b.issuer_bonds.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">
            Same issuer ({b.issuer_bonds.length})
          </h3>
          <div className="rounded-lg border border-zinc-800 divide-y divide-zinc-800/60 max-h-56 overflow-y-auto scroll-slim">
            {b.issuer_bonds.map((s) => (
              <div key={s.isin} className="flex items-center justify-between px-3 py-2 text-xs">
                <span className="font-mono text-zinc-400">{s.nse_symbol ?? s.isin}</span>
                <span className="text-zinc-500">
                  {s.coupon_rate != null ? `${Number(s.coupon_rate).toFixed(2)}%` : ""} ·{" "}
                  {fmt.date(s.redemption_date)} ·{" "}
                  {s.close != null ? fmt.inr(Number(s.close)) : "untraded"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {b.documents.length > 0 && (
        <div className="text-xs text-zinc-500">
          {b.documents
            .filter((d) => d.download_link)
            .slice(0, 5)
            .map((d, i) => (
              <a
                key={i}
                href={d.download_link!}
                target="_blank"
                rel="noreferrer"
                className="text-sky-400 hover:underline mr-3"
              >
                {d.doc_name ?? "document"} ↗
              </a>
            ))}
        </div>
      )}
    </div>
  );
}
