import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { api, fmt } from "../api/client";
import type { HoldingQuote, PortfolioPosition } from "../api/types";
import { fmtIstTime } from "../lib/quoteValuation";
import type { Tab as DrawerTab } from "./BondDrawer";
import ExitTargetEditor from "./ExitTargetEditor";
import { MetadataSeparator } from "./InlineMetadata";
import LotsPanel from "./LotsPanel";
import type { TicketState } from "./OrderBook";

/** What you want to know when you open a position you hold: how each lot
 * is doing, what your units are about to pay (and whether you are already
 * on the register for it), and what you could get out today. */
export default function PositionPanel({
  p,
  quote,
  kiteConnected,
  platforms,
  onSelect,
}: {
  p: PortfolioPosition;
  /** Live best bid / ask for this bond (undefined = Kite not connected). */
  quote?: HoldingQuote | null;
  kiteConnected: boolean;
  platforms?: string[];
  onSelect: (tab?: DrawerTab, ticket?: TicketState) => void;
}) {
  const received = (p.received?.interest ?? 0) + (p.received?.principal ?? 0);
  const sold = ifSoldNow(p, quote);
  return (
    <div className="grid gap-3 items-stretch xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,1fr)]">
      <Card title="Your lots" aside={`${p.lots.length} lot${p.lots.length === 1 ? "" : "s"}`}>
        <LotsPanel p={p} platforms={platforms} />
      </Card>

      <Card
        title="Cashflows"
        aside={`your ${fmt.num(p.units, 2)} unit${p.units === 1 ? "" : "s"}`}
        footer={
          <>
            <span title="Per schedule to date, dated lots on the register by each record date; not confirmation of bank receipt.">
              Received so far {fmt.inr(received)}
              {p.received && (
                <>
                  <MetadataSeparator />
                  {p.received.payments} payment{p.received.payments === 1 ? "" : "s"}
                </>
              )}
            </span>
            <button
              type="button"
              onClick={() => onSelect("cashflows")}
              className="text-emerald-400 hover:text-emerald-300"
            >
              All cashflows →
            </button>
          </>
        }
      >
        <UpcomingList p={p} />
      </Card>

      <Card
        title="Exit"
        aside={
          quote?.bid_price
            ? [quote.bid_exchange ?? quote.exchange, fmtIstTime(quote.quote_at)]
                .filter(Boolean)
                .join(" · ")
            : undefined
        }
        footer={
          kiteConnected && quote?.bid_price ? (
            <>
              <span />
              <button
                type="button"
                title="Opens the order ticket on the Order Book tab, pre-filled to sell into this bid. Nothing is placed until you confirm there."
                onClick={() =>
                  onSelect("order book", {
                    exchange: (quote.bid_exchange ?? quote.exchange ?? "BSE") as "NSE" | "BSE",
                    side: "SELL",
                    price: quote.bid_price,
                    qty: Math.min(p.units, quote.bid_qty ?? p.units),
                  })
                }
                className="rounded border border-red-500/50 px-2.5 py-1 text-[11px] text-red-400 hover:bg-red-500/10"
              >
                Sell at bid
              </button>
            </>
          ) : undefined
        }
      >
        <ExitBody p={p} quote={quote} kiteConnected={kiteConnected} sold={sold} />
      </Card>
    </div>
  );
}

function Card({
  title,
  aside,
  footer,
  children,
}: {
  title: string;
  aside?: string;
  footer?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col rounded-lg border border-zinc-800 bg-zinc-900/40">
      <div className="flex items-center justify-between border-b border-zinc-800 px-3 py-1.5 text-[10px] uppercase tracking-wider text-zinc-500">
        <span className="font-semibold">{title}</span>
        {aside && <span className="normal-case tracking-normal">{aside}</span>}
      </div>
      <div className="flex-1 p-3 text-xs">{children}</div>
      {footer && (
        <div className="flex items-center justify-between border-t border-zinc-800/60 px-3 py-2 text-[11px] text-zinc-500">
          {footer}
        </div>
      )}
    </section>
  );
}

const PILL = "shrink-0 rounded border px-1.5 py-px text-[10px] whitespace-nowrap";

function UpcomingList({ p }: { p: PortfolioPosition }) {
  const upcoming = p.upcoming ?? [];
  if (upcoming.length === 0)
    return <div className="text-zinc-600">No future payments scheduled</div>;
  return (
    <div>
      {upcoming.map((u) => {
        const kind = u.event === "interest" ? "coupon" : "repayment";
        const through = u.record_date ?? u.payment_date ?? u.due_date;
        return (
          <div
            key={`${u.event}-${u.due_date}`}
            className="grid grid-cols-[6.5rem_1fr_auto] items-center gap-2 border-b border-zinc-800/40 py-1.5 last:border-0"
            title={
              u.locked
                ? `Record date ${u.record_date ? fmt.date(u.record_date) : "passed"}: your ${fmt.num(u.units, 2)} units are on the register for this ${kind}.`
                : `Stay on the register through ${fmt.date(through)} (don't sell before it) to receive this ${kind} on your ${fmt.num(u.units, 2)} units.`
            }
          >
            <span className="whitespace-nowrap text-zinc-300">
              {fmt.date(u.payment_date ?? u.due_date)}
            </span>
            <span className="tabular-nums text-zinc-200">
              {u.estimated && "≈"}
              {fmt.inr(u.amount)}
              {u.event === "redemption" && (
                <span className="ml-1.5 rounded bg-zinc-800 px-1 text-[10px] text-zinc-400">
                  principal
                </span>
              )}
            </span>
            {u.locked ? (
              <span className={`${PILL} border-emerald-500/30 bg-emerald-500/10 text-emerald-400`}>
                on register ✓
              </span>
            ) : (
              <span className={`${PILL} border-amber-500/30 bg-amber-500/10 text-amber-400`}>
                hold → {fmt.date(through)}
              </span>
            )}
          </div>
        );
      })}
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

function ExitBody({
  p,
  quote,
  kiteConnected,
  sold,
}: {
  p: PortfolioPosition;
  quote?: HoldingQuote | null;
  kiteConnected: boolean;
  sold: ReturnType<typeof ifSoldNow>;
}) {
  const { data: kite } = useQuery({
    queryKey: ["kite-status"],
    queryFn: api.kite.status,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    enabled: !kiteConnected,
  });
  const row = "grid grid-cols-[6.5rem_1fr] items-baseline gap-2 py-1";
  const label = "text-zinc-500";
  return (
    <div>
      {!kiteConnected ? (
        <div className="mb-2 rounded-md border border-dashed border-zinc-800 px-3 py-2 text-zinc-400">
          Connect Kite to see the live bid and what you'd get if you sold now.
          {kite?.configured && kite.login_url && (
            <>
              {" "}
              <a
                href={kite.login_url}
                className="text-emerald-400 hover:text-emerald-300 whitespace-nowrap"
              >
                Connect Kite →
              </a>
            </>
          )}
        </div>
      ) : (
        <>
          <div className={row}>
            <span className={label}>Best bid</span>
            {quote?.bid_price ? (
              <span className="tabular-nums text-zinc-200">
                {fmt.inrFull(quote.bid_price)} × {fmt.num(quote.bid_qty, 0)}
                {quote.bid_ytm != null && (
                  <span className="text-zinc-500" title="Gross yield a buyer gets at this bid">
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
              <span
                className={`tabular-nums ${sold.gain >= 0 ? "text-emerald-400" : "text-red-400"}`}
              >
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
        </>
      )}
      <div className={row}>
        <span className={label}>Target</span>
        <ExitTargetEditor isin={p.isin} compact />
      </div>
    </div>
  );
}
