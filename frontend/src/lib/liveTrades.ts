import type { DepthSide, OrderBook, Trade } from "../api/types";

/** Today's trading on one exchange, taken from the Kite quote that also
 * feeds the order book. The bhavcopy only lands after the close, so until
 * then this is the only sight of today's trade. */
export type LiveTrade = {
  exchange: "NSE" | "BSE";
  price: number;
  at: string | null; // "YYYY-MM-DD HH:MM:SS" IST
  lastQty: number | null;
  volume: number;
  avgPrice: number | null;
  turnover: number | null;
  ohlc: Record<string, number> | null;
};

export function liveTradesFromBook(book: OrderBook | null | undefined): LiveTrade[] {
  const out: LiveTrade[] = [];
  for (const [exchange, side] of [
    ["NSE", book?.nse],
    ["BSE", book?.bse],
  ] as const) {
    if (!side || side.source !== "kite") continue;
    if (!(side.day_volume && side.day_volume > 0) || side.last_price == null) continue;
    out.push(sideTrade(exchange, side));
  }
  return out;
}

function sideTrade(exchange: "NSE" | "BSE", side: DepthSide): LiveTrade {
  return {
    exchange,
    price: side.last_price as number,
    at: side.last_trade_at ?? null,
    lastQty: side.last_qty ?? null,
    volume: side.day_volume as number,
    avgPrice: side.day_avg_price ?? null,
    turnover: side.day_turnover ?? null,
    ohlc: (side.ohlc as Record<string, number> | null | undefined) ?? null,
  };
}

/** Live rows are shown only while the day's bhavcopy row is absent. */
export function liveRowsToShow(live: LiveTrade[], trades: Trade[], todayIso: string): LiveTrade[] {
  return live.filter(
    (l) =>
      !trades.some(
        (t) => t.trade_date === todayIso && t.exchange === l.exchange && t.segment === "retail",
      ),
  );
}

/** "12:41" from Kite's "2026-10-29 12:41:07". */
export function liveTime(at: string | null): string | null {
  const m = at?.match(/\b(\d{2}:\d{2})(?::\d{2})?$/);
  return m ? m[1] : null;
}

export function todayIst(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(now);
}
