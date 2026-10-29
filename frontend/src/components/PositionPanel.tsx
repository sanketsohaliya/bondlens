import { fmt } from "../api/client";
import type { HoldingQuote, PortfolioPosition } from "../api/types";
import type { Tab as DrawerTab } from "./BondDrawer";
import ExitTargetEditor from "./ExitTargetEditor";
import { MetadataSeparator } from "./InlineMetadata";
import LotsPanel from "./LotsPanel";
import type { TicketState } from "./OrderBook";

const HEAD = "text-[10px] font-semibold uppercase tracking-wider text-zinc-500 mb-1.5";

/** What you want to know when you open a position you hold: how each lot
 * is doing, what your units are about to pay (and whether you are already
 * on the register for it), and what you could get out today. */
export default function PositionPanel({
  p,
  quote,
  kiteConnected,
  onSelect,
}: {
  p: PortfolioPosition;
  /** Live best bid / ask for this bond (undefined = Kite not connected). */
  quote?: HoldingQuote | null;
  kiteConnected: boolean;
  onSelect: (tab?: DrawerTab, ticket?: TicketState) => void;
}) {
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,1fr)]">
      <section>
        <h4 className={HEAD}>Your lots</h4>
        <LotsPanel p={p} />
      </section>
      <section>
        <h4 className={HEAD}>
          Cashflows · your {fmt.num(p.units, 2)} unit{p.units === 1 ? "" : "s"}
        </h4>
        <UpcomingList p={p} onAll={() => onSelect("cashflows")} />
      </section>
      <section>
        <h4 className={HEAD}>Exit</h4>
        <ExitPanel p={p} quote={quote} kiteConnected={kiteConnected} onSelect={onSelect} />
      </section>
    </div>
  );
}

function UpcomingList({ p, onAll }: { p: PortfolioPosition; onAll: () => void }) {
  const received = (p.received?.interest ?? 0) + (p.received?.principal ?? 0);
  const upcoming = p.upcoming ?? [];
  return (
    <div className="text-xs space-y-0.5">
      {upcoming.length === 0 && <div className="text-zinc-600">No future payments scheduled</div>}
      {upcoming.map((u) => (
        <div
          key={`${u.event}-${u.due_date}`}
          className="grid grid-cols-[6.5rem_1fr_auto] gap-2 items-baseline px-1"
          title={
            u.locked
              ? `Record date ${u.record_date ? fmt.date(u.record_date) : "passed"}: your ${fmt.num(u.units, 2)} units are on the register for this ${u.event === "interest" ? "coupon" : "repayment"}.`
              : `Stay on the register through ${u.record_date ? fmt.date(u.record_date) : fmt.date(u.payment_date ?? u.due_date)} (hold, and don't sell before it) to receive this ${u.event === "interest" ? "coupon" : "repayment"} on your ${fmt.num(u.units, 2)} units.`
          }
        >
          <span className="text-zinc-300 whitespace-nowrap">
            {fmt.date(u.payment_date ?? u.due_date)}
          </span>
          <span className="tabular-nums text-zinc-200">
            {u.estimated && "≈"}
            {fmt.inr(u.amount)}
            {u.event === "redemption" && <span className="text-zinc-500"> principal</span>}
          </span>
          <span
            className={`text-[10px] whitespace-nowrap ${u.locked ? "text-emerald-500/80" : "text-amber-500/80"}`}
          >
            {u.locked
              ? "on register ✓"
              : `hold through ${fmt.date(u.record_date ?? u.payment_date ?? u.due_date)}`}
          </span>
        </div>
      ))}
      <div className="flex items-center px-1 pt-1 text-[11px] text-zinc-500">
        <span title="Per schedule to date, dated lots on the register by each record date; not confirmation of bank receipt.">
          Received so far {fmt.inr(received)}
          {p.received && (
            <>
              <MetadataSeparator />
              {p.received.payments} payment{p.received.payments === 1 ? "" : "s"}
            </>
          )}
        </span>
        <MetadataSeparator />
        <button type="button" onClick={onAll} className="text-emerald-400 hover:text-emerald-300">
          All cashflows →
        </button>
      </div>
    </div>
  );
}

/** Best bid x covered units against the cost of those units. */
export function ifSoldNow(p: PortfolioPosition, quote: HoldingQuote | null | undefined) {
  if (!quote?.bid_price || !quote.bid_qty || !p.units || p.avg_price == null) return null;
  const covered = Math.min(p.units, quote.bid_qty);
  const proceeds = covered * quote.bid_price;
  const cost = covered * p.avg_price;
  return {
    covered,
    proceeds,
    gain: proceeds - cost,
    gainPct: cost ? ((proceeds - cost) / cost) * 100 : null,
  };
}

function ExitPanel({
  p,
  quote,
  kiteConnected,
  onSelect,
}: {
  p: PortfolioPosition;
  quote?: HoldingQuote | null;
  kiteConnected: boolean;
  onSelect: (tab?: DrawerTab, ticket?: TicketState) => void;
}) {
  const sold = ifSoldNow(p, quote);
  const row = "grid grid-cols-[6.5rem_1fr] gap-2 items-baseline px-1";
  const label = "text-zinc-500";
  return (
    <div className="text-xs space-y-1">
      <div className={row}>
        <span className={label}>Best bid</span>
        {!kiteConnected ? (
          <span className="text-zinc-500">
            Connect Kite for the live bid
            {p.ltp != null && <span className="text-zinc-600"> · LTP {fmt.inrFull(p.ltp)}</span>}
          </span>
        ) : quote?.bid_price ? (
          <span className="text-zinc-200 tabular-nums">
            {fmt.inrFull(quote.bid_price)} × {fmt.num(quote.bid_qty, 0)}
            {quote.bid_exchange && <span className="text-zinc-500"> on {quote.bid_exchange}</span>}
            {quote.bid_ytm != null && (
              <span className="text-zinc-400" title="Gross yield a buyer gets at this bid">
                {" "}
                {fmt.pct(quote.bid_ytm)}
              </span>
            )}
          </span>
        ) : (
          <span className="text-zinc-500">no bid right now</span>
        )}
      </div>
      <div className={row}>
        <span className={label}>If sold now</span>
        {sold ? (
          <span className={`tabular-nums ${sold.gain >= 0 ? "text-emerald-400" : "text-red-400"}`}>
            {sold.gain >= 0 ? "+" : "−"}
            {fmt.inr(Math.abs(sold.gain))}
            {sold.gainPct != null && (
              <span className="text-[10px] opacity-80"> ({sold.gainPct.toFixed(1)}%)</span>
            )}
            {sold.covered < p.units && (
              <span className="text-zinc-500">
                {" "}
                · bid covers {fmt.num(sold.covered, 2)} of {fmt.num(p.units, 2)} units
              </span>
            )}
          </span>
        ) : (
          <span className="text-zinc-600">–</span>
        )}
      </div>
      <div className={row}>
        <span className={label}>Target</span>
        <ExitTargetEditor isin={p.isin} compact />
      </div>
      <div className="px-1 pt-1">
        <button
          type="button"
          disabled={!quote?.bid_price}
          title={
            quote?.bid_price
              ? "Opens the order ticket on the Order Book tab, pre-filled to sell into this bid. Nothing is placed until you confirm there."
              : "Needs a live bid (Kite connected)"
          }
          onClick={() =>
            quote?.bid_price &&
            onSelect("order book", {
              exchange: (quote.bid_exchange ?? quote.exchange ?? "BSE") as "NSE" | "BSE",
              side: "SELL",
              price: quote.bid_price,
              qty: Math.min(p.units, quote.bid_qty ?? p.units),
            })
          }
          className="text-[11px] px-2 py-0.5 rounded border border-red-500/50 text-red-400 hover:bg-red-500/10 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Sell at bid
        </button>
      </div>
    </div>
  );
}
