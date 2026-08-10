import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, fmt } from "../api/client";
import type { KiteOrder } from "../api/types";

const TD = "px-3 py-2.5";
const TH = "px-3 py-2 font-medium";

const CANCELLABLE = ["OPEN", "TRIGGER PENDING", "AMO REQ RECEIVED"];

/** "2026-08-10 10:04:11" -> "10:04:11 am" (Kite timestamps are IST). */
function timeOf(ts: string | null): string {
  if (!ts) return "–";
  const t = new Date(ts.replace(" ", "T"));
  return Number.isNaN(t.getTime()) ? ts : t.toLocaleTimeString("en-IN");
}

/** Today's orders on the user's own Zerodha account (all instruments,
 * matching Kite's orders page; bond rows link to the bond drawer).
 * Kite is the system of record — nothing is stored app-side. */
export default function Orders({ onSelect }: { onSelect: (isin: string) => void }) {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["kite-orders"],
    queryFn: api.kite.orders,
    refetchInterval: 15_000,
    refetchOnWindowFocus: false,
  });
  const cancel = useMutation({
    mutationFn: (orderId: string) => api.kite.cancelOrder(orderId),
    onSettled: () => qc.invalidateQueries({ queryKey: ["kite-orders"] }),
  });

  if (isLoading) {
    return <div className="text-zinc-500 animate-pulse py-12 text-center">Loading orders…</div>;
  }
  if (error) {
    return (
      <div className="text-zinc-500 py-12 text-center">
        Couldn't load orders — is your Kite session still connected?
      </div>
    );
  }

  const orders = [...(data?.orders ?? [])].sort((a, b) =>
    (b.order_timestamp ?? "").localeCompare(a.order_timestamp ?? ""),
  );

  return (
    <div className="rounded-lg border border-zinc-800 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-zinc-500 border-b border-zinc-800">
              <th className={TH}>Time</th>
              <th className={TH}>Side</th>
              <th className={TH}>Instrument</th>
              <th className={TH}>Exch.</th>
              <th className={`${TH} text-right`}>Qty (filled)</th>
              <th className={`${TH} text-right`}>Price</th>
              <th className={`${TH} text-right`}>Avg</th>
              <th className={TH}>Status</th>
              <th className={`${TH} text-right`} />
            </tr>
          </thead>
          <tbody>
            {orders.map((o) => (
              <OrderRow
                key={o.order_id}
                o={o}
                onSelect={onSelect}
                onCancel={() => cancel.mutate(o.order_id)}
                cancelling={cancel.isPending}
              />
            ))}
            {orders.length === 0 && (
              <tr>
                <td colSpan={9} className="px-3 py-10 text-center text-zinc-600">
                  No orders today.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="px-3 py-2 border-t border-zinc-800 text-[11px] text-zinc-600">
        Today's orders on your Zerodha account, refreshed every 15s. Kite keeps the full history.
      </div>
    </div>
  );
}

function OrderRow({
  o,
  onSelect,
  onCancel,
  cancelling,
}: {
  o: KiteOrder;
  onSelect: (isin: string) => void;
  onCancel: () => void;
  cancelling: boolean;
}) {
  const rejected = o.status === "REJECTED" || o.status === "CANCELLED";
  return (
    <tr className="border-b border-zinc-800/60 hover:bg-zinc-900/60">
      <td className={`${TD} text-zinc-400 whitespace-nowrap`}>{timeOf(o.order_timestamp)}</td>
      <td
        className={`${TD} font-medium ${o.transaction_type === "BUY" ? "text-emerald-400" : "text-red-400"}`}
      >
        {o.transaction_type}
      </td>
      <td className={TD}>
        {o.isin ? (
          <button
            type="button"
            title="View bond details"
            onClick={() => onSelect(o.isin as string)}
            className="block font-medium text-zinc-200 max-w-64 truncate text-left hover:text-emerald-400 hover:underline underline-offset-2"
          >
            {o.issuer_name ?? o.tradingsymbol}
          </button>
        ) : (
          <span className="text-zinc-300">{o.tradingsymbol}</span>
        )}
        <div className="text-[10px] text-zinc-600">{o.isin ?? ""}</div>
      </td>
      <td className={`${TD} text-zinc-400`}>{o.exchange}</td>
      <td className={`${TD} text-right tabular-nums text-zinc-300`}>
        {fmt.num(o.quantity, 0)} ({fmt.num(o.filled_quantity ?? 0, 0)})
      </td>
      <td className={`${TD} text-right tabular-nums text-zinc-300`}>{fmt.inrFull(o.price)}</td>
      <td className={`${TD} text-right tabular-nums text-zinc-500`}>
        {o.average_price ? fmt.inrFull(o.average_price) : "–"}
      </td>
      <td
        className={`${TD} ${rejected ? "text-red-400/80" : o.status === "COMPLETE" ? "text-emerald-400/80" : "text-zinc-400"}`}
        title={o.status_message ?? undefined}
      >
        {o.status}
      </td>
      <td className={`${TD} text-right`}>
        {o.status != null && CANCELLABLE.includes(o.status) && (
          <button
            type="button"
            onClick={onCancel}
            disabled={cancelling}
            className="text-[11px] px-2 py-0.5 rounded border border-zinc-700 text-zinc-400 hover:border-red-500/50 hover:text-red-400 disabled:opacity-50"
          >
            Cancel
          </button>
        )}
      </td>
    </tr>
  );
}
