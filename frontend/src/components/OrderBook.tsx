import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, fmt } from "../api/client";
import type { DepthSide, KiteOrder, YtmAtPrice } from "../api/types";
import { isQuoteFresh, useQuoteClock, valuationIsCurrent } from "../lib/quoteValuation";
import { useDepthStream } from "../lib/useDepthStream";
import { MetadataSeparator } from "./InlineMetadata";
import OrderTicket from "./OrderTicket";
import YieldQuote from "./YieldQuote";

function nseQuoteUrl(symbol: string, series: string | null | undefined): string {
  const qs = new URLSearchParams({ symbol });
  if (series) qs.set("series", series);
  return `https://www.nseindia.com/get-quotes/bonds?${qs}`;
}

interface TicketState {
  exchange: "NSE" | "BSE";
  side: "BUY" | "SELL";
  price?: number | null;
  qty?: number | null;
  /** Set when editing an existing open order instead of placing one. */
  modifyOrderId?: string;
}

/** Live top-5 bid/ask depth per exchange. With the user's Kite account
 * connected the ladder streams from Zerodha's ticker and rows are
 * orderable; otherwise it's the REST snapshot (server caches 30s) with
 * a manual Refresh. */
export default function OrderBook({
  isin,
  nseSymbol,
  nseSeries,
}: {
  isin: string;
  nseSymbol?: string | null;
  nseSeries?: string | null;
}) {
  const { data: kite } = useQuery({
    queryKey: ["kite-status"],
    queryFn: api.kite.status,
    staleTime: 60_000,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
  const connected = kite?.connected ?? false;

  const {
    data: rest,
    isLoading,
    isFetching,
    refetch,
  } = useQuery({
    queryKey: ["depth", isin],
    queryFn: () => api.depth(isin),
    staleTime: 25_000,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
  const now = useQuoteClock();
  const stream = useDepthStream(isin, connected);
  const streamAge = now - Date.parse(stream.book?.fetched_at ?? "");
  const recentStream = streamAge >= -30_000 && streamAge < 90_000;
  const data = recentStream ? stream.book : (rest ?? stream.book);
  // A connected socket alone is not live depth: require a recent tick
  // with a usable snapshot. Each exchange keeps its own capture time,
  // so a tick on one venue cannot rejuvenate stale depth on the other.
  const live =
    stream.status === "live" &&
    recentStream &&
    [stream.book?.nse, stream.book?.bse].some((side) => isQuoteFresh(side, now));
  const showRefresh = !live;

  const [ticket, setTicket] = useState<TicketState | null>(null);

  if (isLoading && !data) {
    return <div className="text-zinc-500 animate-pulse py-8 text-center">Fetching depth…</div>;
  }
  if (!data) {
    return <div className="text-zinc-500 py-8 text-center">Order book unavailable.</div>;
  }

  const empty = !data.nse && !data.bse;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-xs text-zinc-500 flex items-center">
          {live ? (
            <>
              <span className="inline-flex items-center gap-1.5 text-emerald-400 font-medium">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-60" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
                </span>
                LIVE
              </span>
              <MetadataSeparator />
              <span>streaming from your Kite session</span>
            </>
          ) : (
            <span>
              Snapshot captured{" "}
              {new Date(data.fetched_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST
              <MetadataSeparator />
              not a guaranteed executable offer
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {kite?.configured && !connected && kite.login_url && (
            <a
              href={kite.login_url}
              className="text-xs px-3 py-1.5 rounded-md border border-amber-500/50 text-amber-400 hover:border-amber-400 hover:text-amber-300 transition-colors"
              title="Log in with your own Zerodha account for live depth and order placement (token lasts one trading day)"
            >
              Connect Kite for live depth
            </a>
          )}
          {connected && <KiteConnectedBadge />}
          {showRefresh && (
            <button
              type="button"
              onClick={() => refetch()}
              disabled={isFetching}
              className="text-xs px-3 py-1.5 rounded-md border border-zinc-700 text-zinc-300 hover:border-zinc-500 hover:text-zinc-100 transition-colors disabled:opacity-50"
            >
              {isFetching ? "Refreshing…" : "Refresh"}
            </button>
          )}
        </div>
      </div>

      {empty ? (
        <div className="text-zinc-500 py-8 text-center">
          No usable depth returned by either source.
          <div className="text-xs mt-1 text-zinc-600">
            This can reflect an empty book, a closed session or unavailable data. Check the exchange
            calendar and broker.
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          {/* Stacked, with whichever exchange actually has depth on top
              (BSE wins ties — NSE depth is usually blocked anyway). */}
          {[
            { name: "BSE" as const, side: data.bse, fallbackUrl: undefined as string | undefined },
            {
              name: "NSE" as const,
              side: data.nse,
              fallbackUrl: nseSymbol ? nseQuoteUrl(nseSymbol, nseSeries) : undefined,
            },
          ]
            .sort((a, b) => Number(b.side != null) - Number(a.side != null))
            .map((x) => (
              <Ladder
                key={x.name}
                name={x.name}
                side={x.side}
                ytmAtAsk={data.ytm_at_ask}
                now={now}
                fallbackUrl={x.fallbackUrl}
                onOrder={connected ? (t) => setTicket({ ...t, exchange: x.name }) : undefined}
              />
            ))}
        </div>
      )}

      {connected && <OrdersStrip isin={isin} onModify={setTicket} />}

      <p className="text-[11px] text-zinc-600">
        Exchange order books are anonymous — no broker/app identity is published. Ask rows show the
        indicative gross YTM or conditional YTC at that price, before fees and taxes. Quantities are
        units; Ticket = price × quantity at that level. Neither the quote nor the modeled payments
        are guaranteed.
      </p>

      {ticket && (
        <OrderTicket
          isin={isin}
          exchange={ticket.exchange}
          initialSide={ticket.side}
          initialPrice={ticket.price}
          initialQty={ticket.qty}
          ytmAtAsk={data.ytm_at_ask}
          modifyOrderId={ticket.modifyOrderId}
          onClose={() => setTicket(null)}
        />
      )}
    </div>
  );
}

function KiteConnectedBadge() {
  const qc = useQueryClient();
  const disconnect = useMutation({
    mutationFn: api.kite.disconnect,
    onSettled: () => qc.invalidateQueries({ queryKey: ["kite-status"] }),
  });
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-zinc-500">
      <span className="text-emerald-500">●</span> Kite connected
      <button
        type="button"
        onClick={() => disconnect.mutate()}
        className="text-zinc-600 hover:text-zinc-300 underline decoration-dotted"
        title="Forget this Kite session (reconnect any time)"
      >
        disconnect
      </button>
    </span>
  );
}

/** Rupee outlay to fully take a depth level: price × qty. */
function ticket(level: { price: number | null; qty: number | null } | undefined): string {
  if (level?.price == null || level.qty == null) return "";
  return fmt.inrFull(level.price * level.qty);
}

function Ladder({
  name,
  side,
  ytmAtAsk,
  now,
  fallbackUrl,
  onOrder,
}: {
  name: "NSE" | "BSE";
  side: DepthSide | null;
  ytmAtAsk: YtmAtPrice[];
  now: number;
  fallbackUrl?: string;
  onOrder?: (t: { side: "BUY" | "SELL"; price?: number | null; qty?: number | null }) => void;
}) {
  const fresh = isQuoteFresh(side, now);
  const ytmFor = (price: number | null) =>
    price == null ? null : ytmAtAsk.find((value) => value.price === price);
  const bestAsk = fresh
    ? side?.asks.find((level) => (level.price ?? 0) > 0 && (level.qty ?? 0) > 0)
    : undefined;
  const bestBid = fresh
    ? side?.bids.find((level) => (level.price ?? 0) > 0 && (level.qty ?? 0) > 0)
    : undefined;

  return (
    <div className="border border-zinc-800 rounded-lg overflow-hidden">
      <div className="px-3 py-2 bg-zinc-900 flex items-center justify-between">
        <span className="text-sm font-semibold flex items-center gap-2">
          {name}
          {side?.source === "kite" && (
            <span className="text-[10px] font-normal px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
              via Kite
            </span>
          )}
        </span>
        <span className="flex items-center gap-2">
          {onOrder && side && (
            <>
              <button
                type="button"
                onClick={() => onOrder({ side: "BUY", price: bestAsk?.price, qty: bestAsk?.qty })}
                className="text-[11px] px-2 py-0.5 rounded border border-emerald-500/50 text-emerald-400 hover:bg-emerald-500/10"
              >
                Buy
              </button>
              <button
                type="button"
                onClick={() => onOrder({ side: "SELL", price: bestBid?.price, qty: bestBid?.qty })}
                className="text-[11px] px-2 py-0.5 rounded border border-red-500/50 text-red-400 hover:bg-red-500/10"
              >
                Sell
              </button>
            </>
          )}
          {side && (
            <span
              className="text-[10px] text-zinc-500"
              title={(side.warnings ?? []).map((warning) => warning.message).join("\n")}
            >
              {fresh ? "Snapshot" : "Expired snapshot"}
              <MetadataSeparator />
              {side.quote_at ?? side.fetched_at ?? "time unknown"}
            </span>
          )}
        </span>
      </div>
      {!side ? (
        <div className="p-4 text-sm text-zinc-600">
          {name === "NSE" ? "Depth unavailable (NSE blocks non-browser access)." : "No depth."}
          {fallbackUrl && (
            <a
              href={fallbackUrl}
              target="_blank"
              rel="noreferrer"
              className="block mt-2 text-emerald-400 hover:text-emerald-300"
            >
              View live order book on nseindia.com ↗
            </a>
          )}
        </div>
      ) : (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-zinc-500 border-b border-zinc-800">
              <th className="px-2 py-1.5 text-right font-normal">Buying at</th>
              <th className="px-2 py-1.5 text-right font-normal">Qty.</th>
              <th className="px-2 py-1.5 text-right font-normal">Ticket</th>
              <th className="px-2 py-1.5 text-right font-normal">Selling at</th>
              <th className="px-2 py-1.5 text-right font-normal">Qty.</th>
              <th className="px-2 py-1.5 text-right font-normal">Ticket</th>
              <th className="px-2 py-1.5 text-right font-normal">YTM @ ask</th>
            </tr>
          </thead>
          <tbody>
            {/* Fixed minimum of 5 rows (broker-app style): short books pad
                with ₹0/0 so the ladder height and eye-line stay constant. */}
            {Array.from({ length: Math.max(5, side.bids.length, side.asks.length) }).map((_, i) => {
              const bid = side.bids[i];
              const ask = side.asks[i];
              const ytm = ask && (ask.qty ?? 0) > 0 ? ytmFor(ask.price) : null;
              return (
                <tr key={i} className="border-b border-zinc-900">
                  <td
                    className={
                      `px-2 py-1.5 text-right font-medium ${bid?.price != null ? "text-emerald-400" : "text-zinc-600"}` +
                      (onOrder && bid?.price != null ? " cursor-pointer hover:underline" : "")
                    }
                    title={onOrder && bid?.price != null ? "Sell at this bid" : undefined}
                    onClick={() =>
                      onOrder &&
                      fresh &&
                      (bid?.qty ?? 0) > 0 &&
                      bid?.price != null &&
                      onOrder({ side: "SELL", price: bid.price, qty: bid.qty })
                    }
                  >
                    {bid?.price != null ? fmt.inrFull(bid.price) : "₹0"}
                  </td>
                  <td
                    className={`px-2 py-1.5 text-right ${bid?.qty != null ? "text-zinc-400" : "text-zinc-600"}`}
                  >
                    {bid?.qty != null ? fmt.num(bid.qty, 0) : "0"}
                  </td>
                  <td className="px-2 py-1.5 text-right text-zinc-500">{ticket(bid) || "₹0"}</td>
                  <td
                    className={
                      `px-2 py-1.5 text-right font-medium ${ask?.price != null ? "text-red-400" : "text-zinc-600"}` +
                      (onOrder && ask?.price != null ? " cursor-pointer hover:underline" : "")
                    }
                    title={onOrder && ask?.price != null ? "Buy at this ask" : undefined}
                    onClick={() =>
                      onOrder &&
                      fresh &&
                      (ask?.qty ?? 0) > 0 &&
                      ask?.price != null &&
                      onOrder({ side: "BUY", price: ask.price, qty: ask.qty })
                    }
                  >
                    {ask?.price != null ? fmt.inrFull(ask.price) : "₹0"}
                  </td>
                  <td
                    className={`px-2 py-1.5 text-right ${ask?.qty != null ? "text-zinc-400" : "text-zinc-600"}`}
                  >
                    {ask?.qty != null ? fmt.num(ask.qty, 0) : "0"}
                  </td>
                  <td className="px-2 py-1.5 text-right text-zinc-500">{ticket(ask) || "₹0"}</td>
                  <td className="px-2 py-1.5 text-right text-zinc-300">
                    {ytm && (
                      <YieldQuote value={ytm} stale={!fresh || !valuationIsCurrent(ytm, now)} />
                    )}
                  </td>
                </tr>
              );
            })}
            <tr className="text-zinc-500">
              <td className="px-2 py-1.5 text-right text-[10px]">total bid</td>
              <td className="px-2 py-1.5 text-right">{fmt.num(side.total_bid_qty, 0)}</td>
              <td />
              <td className="px-2 py-1.5 text-right text-[10px]">total ask</td>
              <td className="px-2 py-1.5 text-right">{fmt.num(side.total_ask_qty, 0)}</td>
              <td />
              <td />
            </tr>
          </tbody>
        </table>
      )}
    </div>
  );
}

/** Today's Kite orders for THIS bond (order placement is per-user, on
 * their own Zerodha account; Kite is the system of record). */
function OrdersStrip({ isin, onModify }: { isin: string; onModify: (t: TicketState) => void }) {
  const qc = useQueryClient();
  const { data, isError, dataUpdatedAt } = useQuery({
    queryKey: ["kite-orders"],
    queryFn: api.kite.orders,
    refetchInterval: 15_000,
    refetchOnWindowFocus: false,
  });
  const cancel = useMutation({
    mutationFn: (orderId: string) => api.kite.cancelOrder(orderId),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["kite-orders"] });
      qc.invalidateQueries({ queryKey: ["kite-margins"] });
    },
  });
  // Refresh after imports and partial reconciliation failures: earlier
  // orders in a failed batch may already have committed their own lots.
  const newLots = data?.new_lots ?? 0;
  const pending = data?.reconciliation_pending ?? false;
  useEffect(() => {
    if (dataUpdatedAt > 0 && (newLots > 0 || pending)) {
      qc.invalidateQueries({ queryKey: ["portfolio"] });
      qc.invalidateQueries({ queryKey: ["lists"] });
      qc.invalidateQueries({ queryKey: ["notifications"] });
    }
  }, [newLots, pending, dataUpdatedAt, qc]);

  // The server resolves each order's tradingsymbol back to its ISIN.
  const mine = (data?.orders ?? []).filter((o) => o.isin === isin);
  if (isError)
    return (
      <div className="text-xs text-amber-400">
        Orders could not be refreshed. Check your broker before retrying an order.
      </div>
    );
  if (mine.length === 0) return null;

  const cancellable = (o: KiteOrder) =>
    o.status != null && ["OPEN", "TRIGGER PENDING", "AMO REQ RECEIVED"].includes(o.status);

  return (
    <div className="border border-zinc-800 rounded-lg overflow-hidden">
      <div className="px-3 py-2 bg-zinc-900 text-sm font-semibold">Your orders today</div>
      {data?.reconciliation_pending && (
        <div className="p-3 text-xs text-amber-400">
          Portfolio reconciliation is pending and will retry. Check holdings before adding fills
          manually.
        </div>
      )}
      <table className="w-full text-xs">
        <thead>
          <tr className="text-zinc-500 border-b border-zinc-800">
            <th className="px-2 py-1.5 text-left font-normal">Side</th>
            <th className="px-2 py-1.5 text-left font-normal">Exch.</th>
            <th className="px-2 py-1.5 text-right font-normal">Qty (filled)</th>
            <th className="px-2 py-1.5 text-right font-normal">Price</th>
            <th className="px-2 py-1.5 text-left font-normal">Status</th>
            <th className="px-2 py-1.5 text-right font-normal" />
          </tr>
        </thead>
        <tbody>
          {mine.map((o) => (
            <tr
              key={o.order_id}
              className="border-b border-zinc-900"
              title={o.status_message ?? undefined}
            >
              <td
                className={`px-2 py-1.5 font-medium ${o.transaction_type === "BUY" ? "text-emerald-400" : "text-red-400"}`}
              >
                {o.transaction_type}
              </td>
              <td className="px-2 py-1.5 text-zinc-400">{o.exchange}</td>
              <td className="px-2 py-1.5 text-right text-zinc-300">
                {fmt.num(o.quantity, 0)} ({fmt.num(o.filled_quantity ?? 0, 0)})
              </td>
              <td className="px-2 py-1.5 text-right text-zinc-300">{fmt.inrFull(o.price)}</td>
              <td className="px-2 py-1.5 text-zinc-400">{o.status}</td>
              <td className="px-2 py-1.5 text-right whitespace-nowrap">
                {cancellable(o) && (
                  <>
                    <button
                      type="button"
                      onClick={() =>
                        onModify({
                          exchange: (o.exchange as "NSE" | "BSE") ?? "BSE",
                          side: (o.transaction_type as "BUY" | "SELL") ?? "BUY",
                          price: o.price,
                          qty: o.quantity,
                          modifyOrderId: o.order_id,
                        })
                      }
                      className="text-[11px] px-2 py-0.5 rounded border border-zinc-700 text-zinc-400 hover:border-emerald-500/50 hover:text-emerald-400"
                    >
                      Modify
                    </button>
                    <button
                      type="button"
                      onClick={() => cancel.mutate(o.order_id)}
                      disabled={cancel.isPending}
                      className="ml-1.5 text-[11px] px-2 py-0.5 rounded border border-zinc-700 text-zinc-400 hover:border-red-500/50 hover:text-red-400 disabled:opacity-50"
                    >
                      Cancel
                    </button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
