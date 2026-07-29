import { useQuery } from "@tanstack/react-query";
import { api, fmt } from "../api/client";
import type { DepthSide, YtmAtPrice } from "../api/types";

/** Live top-5 bid/ask depth per exchange. Fetched once when the tab is
 * opened (server caches 30s); no background polling — use Refresh. */
export default function OrderBook({ isin }: { isin: string }) {
  const { data, isLoading, isFetching, refetch, dataUpdatedAt } = useQuery({
    queryKey: ["depth", isin],
    queryFn: () => api.depth(isin),
    staleTime: 25_000,
    refetchOnWindowFocus: false,
  });

  if (isLoading) {
    return <div className="text-zinc-500 animate-pulse py-8 text-center">Fetching depth…</div>;
  }
  if (!data) {
    return <div className="text-zinc-500 py-8 text-center">Order book unavailable.</div>;
  }

  const empty = !data.nse && !data.bse;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-xs text-zinc-500">
          As of {new Date(dataUpdatedAt).toLocaleTimeString("en-IN")} · fetched live on open, not
          streaming
        </div>
        <button
          type="button"
          onClick={() => refetch()}
          disabled={isFetching}
          className="text-xs px-3 py-1.5 rounded-md border border-zinc-700 text-zinc-300 hover:border-zinc-500 hover:text-zinc-100 transition-colors disabled:opacity-50"
        >
          {isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {empty ? (
        <div className="text-zinc-500 py-8 text-center">
          No resting orders on either exchange.
          {!marketOpenNow() && (
            <div className="text-xs mt-1 text-zinc-600">
              Market is closed (NSE/BSE trade 09:15–15:30 IST, Mon–Fri) — order books empty out
              after hours.
            </div>
          )}
        </div>
      ) : (
        <div className="grid md:grid-cols-2 gap-4">
          <Ladder name="NSE" side={data.nse} ytmAtAsk={data.ytm_at_ask} />
          <Ladder name="BSE" side={data.bse} ytmAtAsk={data.ytm_at_ask} />
        </div>
      )}

      <p className="text-[11px] text-zinc-600">
        Exchange order books are anonymous — no broker/app identity is published. Ask rows show the
        YTM locked if bought at that price. Quantities are units.
      </p>
    </div>
  );
}

function marketOpenNow(): boolean {
  const ist = new Date(Date.now() + (330 + new Date().getTimezoneOffset()) * 60_000);
  const day = ist.getDay();
  const mins = ist.getHours() * 60 + ist.getMinutes();
  return day >= 1 && day <= 5 && mins >= 555 && mins <= 930;
}

function Ladder({
  name,
  side,
  ytmAtAsk,
}: {
  name: string;
  side: DepthSide | null;
  ytmAtAsk: YtmAtPrice[];
}) {
  const ytmFor = (price: number | null) =>
    price == null ? null : (ytmAtAsk.find((y) => y.price === price)?.ytm ?? null);

  return (
    <div className="border border-zinc-800 rounded-lg overflow-hidden">
      <div className="px-3 py-2 bg-zinc-900 flex items-center justify-between">
        <span className="text-sm font-semibold">{name}</span>
        {side?.as_of && <span className="text-[10px] text-zinc-500">{side.as_of}</span>}
      </div>
      {!side ? (
        <div className="p-4 text-sm text-zinc-600">
          {name === "NSE" ? "Depth unavailable (NSE blocks non-browser access)." : "No depth."}
        </div>
      ) : (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-zinc-500 border-b border-zinc-800">
              <th className="px-2 py-1.5 text-right font-normal">Bid qty</th>
              <th className="px-2 py-1.5 text-right font-normal">Bid</th>
              <th className="px-2 py-1.5 text-right font-normal">Ask</th>
              <th className="px-2 py-1.5 text-right font-normal">Ask qty</th>
              <th className="px-2 py-1.5 text-right font-normal">YTM @ ask</th>
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: Math.max(side.bids.length, side.asks.length) }).map((_, i) => {
              const bid = side.bids[i];
              const ask = side.asks[i];
              const ytm = ask ? ytmFor(ask.price) : null;
              return (
                <tr key={i} className="border-b border-zinc-900">
                  <td className="px-2 py-1.5 text-right text-zinc-400">
                    {bid?.qty != null ? fmt.num(bid.qty, 0) : ""}
                  </td>
                  <td className="px-2 py-1.5 text-right text-emerald-400 font-medium">
                    {bid?.price != null ? fmt.inrFull(bid.price) : ""}
                  </td>
                  <td className="px-2 py-1.5 text-right text-red-400 font-medium">
                    {ask?.price != null ? fmt.inrFull(ask.price) : ""}
                  </td>
                  <td className="px-2 py-1.5 text-right text-zinc-400">
                    {ask?.qty != null ? fmt.num(ask.qty, 0) : ""}
                  </td>
                  <td className="px-2 py-1.5 text-right text-zinc-300">
                    {ytm != null ? fmt.pct(ytm) : ""}
                  </td>
                </tr>
              );
            })}
            <tr className="text-zinc-500">
              <td className="px-2 py-1.5 text-right">{fmt.num(side.total_bid_qty, 0)}</td>
              <td className="px-2 py-1.5 text-right text-[10px]">total bid</td>
              <td className="px-2 py-1.5 text-right text-[10px]">total ask</td>
              <td className="px-2 py-1.5 text-right">{fmt.num(side.total_ask_qty, 0)}</td>
              <td />
            </tr>
          </tbody>
        </table>
      )}
    </div>
  );
}
