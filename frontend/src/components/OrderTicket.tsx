import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, fmt } from "../api/client";
import type { YtmAtPrice } from "../api/types";

/** Kite-style order dialog: LIMIT/CNC/DAY only (market orders on
 * illiquid bond books are dangerous). Real money — an explicit confirm
 * step always shows exactly what will be sent before submitting.
 * With `modifyOrderId` set the same dialog edits an open order's
 * quantity/price instead (side can't change on a Kite modify). */
export default function OrderTicket({
  isin,
  exchange,
  initialSide,
  initialPrice,
  initialQty,
  ytmAtAsk,
  modifyOrderId,
  label,
  onClose,
}: {
  isin: string;
  exchange: "NSE" | "BSE";
  initialSide: "BUY" | "SELL";
  initialPrice?: number | null;
  initialQty?: number | null;
  ytmAtAsk: YtmAtPrice[];
  /** Open order to modify; unset = place a new order. */
  modifyOrderId?: string;
  /** Display name (issuer/tradingsymbol) when opened away from a bond. */
  label?: string;
  onClose: () => void;
}) {
  const modify = modifyOrderId != null;
  const [side, setSide] = useState<"BUY" | "SELL">(initialSide);
  const [qty, setQty] = useState(String(initialQty ?? 1));
  const [price, setPrice] = useState(initialPrice != null ? String(initialPrice) : "");
  const [confirming, setConfirming] = useState(false);
  const qc = useQueryClient();

  const quantity = Math.floor(Number(qty));
  const limitPrice = Number(price);
  const valid = quantity > 0 && Number.isFinite(limitPrice) && limitPrice > 0;
  const outlay = valid ? quantity * limitPrice : null;
  const ytm = ytmAtAsk.find((y) => y.price === limitPrice)?.ytm ?? null;

  const { data: funds } = useQuery({
    queryKey: ["kite-margins"],
    queryFn: api.kite.margins,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
  const buy = side === "BUY";
  const available = funds?.available_margin ?? null;
  const overMargin = buy && outlay != null && available != null && outlay > available;

  const submit = useMutation({
    mutationFn: () =>
      modify
        ? api.kite.modifyOrder(modifyOrderId, { quantity, price: limitPrice })
        : api.kite.placeOrder({
            isin,
            exchange,
            transaction_type: side,
            quantity,
            price: limitPrice,
          }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kite-orders"] });
      qc.invalidateQueries({ queryKey: ["kite-margins"] });
    },
  });

  // Tailwind needs literal class names (no runtime interpolation).
  const accentBtn = buy ? "bg-emerald-500 hover:bg-emerald-400" : "bg-red-500 hover:bg-red-400";
  const verb = modify ? "MODIFY" : side;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} />
      <div className="relative w-96 max-w-[95vw] bg-zinc-950 border border-zinc-700 rounded-lg shadow-xl">
        <div
          className={`px-4 py-3 rounded-t-lg flex items-center justify-between ${buy ? "bg-emerald-500/15" : "bg-red-500/15"}`}
        >
          <div className="text-sm font-semibold truncate">
            {modify && "MODIFY · "}
            {side} · {label ?? isin} · {exchange}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-zinc-500 hover:text-zinc-200 text-lg leading-none"
          >
            ×
          </button>
        </div>

        {submit.isSuccess ? (
          <div className="p-4 space-y-3">
            <div className="text-emerald-400 text-sm font-medium">
              {modify ? "Order modified on Zerodha." : "Order placed on Zerodha."}
            </div>
            <div className="text-xs text-zinc-400 font-mono">Order ID: {submit.data.order_id}</div>
            <div className="text-[11px] text-zinc-600">
              Track fills / reject reasons in the orders list or on Kite.
            </div>
            <button
              type="button"
              onClick={onClose}
              className="w-full py-2 rounded-md border border-zinc-700 text-sm text-zinc-300 hover:border-zinc-500"
            >
              Done
            </button>
          </div>
        ) : (
          <div className="p-4 space-y-3">
            {modify ? (
              <div
                className={`py-1.5 text-center rounded-md text-sm font-medium border ${
                  buy
                    ? "border-emerald-500 text-emerald-400 bg-emerald-500/10"
                    : "border-red-500 text-red-400 bg-red-500/10"
                }`}
              >
                {side}
              </div>
            ) : (
              <div className="flex gap-1">
                {(["BUY", "SELL"] as const).map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => {
                      setSide(s);
                      setConfirming(false);
                    }}
                    className={
                      "flex-1 py-1.5 rounded-md text-sm font-medium border transition-colors " +
                      (side === s
                        ? s === "BUY"
                          ? "border-emerald-500 text-emerald-400 bg-emerald-500/10"
                          : "border-red-500 text-red-400 bg-red-500/10"
                        : "border-zinc-800 text-zinc-500 hover:text-zinc-300")
                    }
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}

            <label className="block text-xs text-zinc-500">
              Quantity (units)
              <input
                type="number"
                min={1}
                step={1}
                value={qty}
                onChange={(e) => {
                  setQty(e.target.value);
                  setConfirming(false);
                }}
                className="mt-1 w-full bg-zinc-900 border border-zinc-700 rounded-md px-3 py-2 text-sm text-zinc-100 focus:border-zinc-500 outline-none"
              />
            </label>
            <label className="block text-xs text-zinc-500">
              Limit price (₹, dirty)
              <input
                type="number"
                min={0}
                step={0.01}
                value={price}
                onChange={(e) => {
                  setPrice(e.target.value);
                  setConfirming(false);
                }}
                className="mt-1 w-full bg-zinc-900 border border-zinc-700 rounded-md px-3 py-2 text-sm text-zinc-100 focus:border-zinc-500 outline-none"
              />
            </label>

            <div className="text-xs text-zinc-400 space-y-1">
              <div className="flex justify-between">
                <span>{buy ? "Outlay" : "Proceeds"}</span>
                <span className="text-zinc-200">{outlay != null ? fmt.inrFull(outlay) : "–"}</span>
              </div>
              {buy && available != null && (
                <div className="flex justify-between">
                  <span>Available margin</span>
                  <span
                    className={overMargin ? "text-red-400" : "text-zinc-200"}
                    title={
                      overMargin
                        ? "Outlay exceeds available margin — Zerodha will likely reject this"
                        : undefined
                    }
                  >
                    {fmt.inrFull(available)}
                  </span>
                </div>
              )}
              {buy && ytm != null && (
                <div className="flex justify-between">
                  <span>YTM locked at this price</span>
                  <span className="text-zinc-200">{fmt.pct(ytm)}</span>
                </div>
              )}
              <div className="flex justify-between text-zinc-600">
                <span>Order type</span>
                <span>LIMIT · CNC · DAY</span>
              </div>
            </div>

            {submit.isError && (
              <div className="text-xs text-red-400 border border-red-500/30 bg-red-500/10 rounded-md px-3 py-2">
                {errorDetail(submit.error)}
              </div>
            )}

            {confirming ? (
              <div className="space-y-2">
                <div className="text-xs text-zinc-300 border border-zinc-700 rounded-md px-3 py-2">
                  {verb} {!modify ? "" : `${side} `}
                  {quantity} × {fmt.inrFull(limitPrice)} on {exchange} = {fmt.inrFull(outlay ?? 0)}{" "}
                  · LIMIT · CNC · DAY
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setConfirming(false)}
                    className="flex-1 py-2 rounded-md border border-zinc-700 text-sm text-zinc-300 hover:border-zinc-500"
                  >
                    Back
                  </button>
                  <button
                    type="button"
                    disabled={submit.isPending}
                    onClick={() => submit.mutate()}
                    className={`flex-1 py-2 rounded-md text-sm font-semibold text-zinc-950 disabled:opacity-50 ${accentBtn}`}
                  >
                    {submit.isPending ? (modify ? "Modifying…" : "Placing…") : `Confirm ${verb}`}
                  </button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                disabled={!valid}
                onClick={() => setConfirming(true)}
                className={`w-full py-2 rounded-md text-sm font-semibold text-zinc-950 disabled:opacity-50 ${accentBtn}`}
              >
                Review {verb}
              </button>
            )}

            <p className="text-[11px] text-zinc-600">
              {modify
                ? "Modifies the open order on your Zerodha account."
                : "Placed on your own Zerodha account."}{" "}
              Zerodha's margin/RMS checks apply; rejections show their reason here.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

/** client.ts throws `Error("<status> <body>")`; body is usually
 * FastAPI's {"detail": "..."} — surface just the detail. */
function errorDetail(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  const json = msg.slice(msg.indexOf(" ") + 1);
  try {
    const parsed = JSON.parse(json);
    if (typeof parsed?.detail === "string") return parsed.detail;
  } catch {
    // not JSON — fall through
  }
  return msg;
}
