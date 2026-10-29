import { describe, expect, it } from "vitest";
import type { OrderBook, Trade } from "../api/types";
import { liveRowsToShow, liveTime, liveTradesFromBook, todayIst } from "./liveTrades";

const side = (over: Record<string, unknown>) =>
  ({
    bids: [],
    asks: [],
    total_bid_qty: null,
    total_ask_qty: null,
    as_of: null,
    source: "kite",
    warnings: [],
    ...over,
  }) as never;

describe("live trades from the order book", () => {
  it("takes traded Kite sides only", () => {
    const book = {
      isin: "X",
      fetched_at: "",
      ytm_at_ask: [],
      nse: side({ source: "exchange", last_price: 1, day_volume: 3 }),
      bse: side({
        last_price: 5094.4,
        last_trade_at: "2026-10-29 12:41:07",
        last_qty: 2,
        day_volume: 2,
        day_avg_price: 5094.4,
        day_turnover: 10188.8,
        ohlc: { high: 5094.4 },
      }),
    } as unknown as OrderBook;
    const live = liveTradesFromBook(book);
    expect(live).toHaveLength(1);
    expect(live[0]).toMatchObject({ exchange: "BSE", price: 5094.4, volume: 2, turnover: 10188.8 });
    expect(liveTime(live[0].at)).toBe("12:41");
    expect(liveTradesFromBook({ ...book, bse: side({ day_volume: 0, last_price: 5 }) })).toEqual(
      [],
    );
  });

  it("hides the live row once the bhavcopy row for today exists", () => {
    const live = liveTradesFromBook({
      isin: "X",
      fetched_at: "",
      ytm_at_ask: [],
      nse: null,
      bse: side({ last_price: 10, day_volume: 1 }),
    } as unknown as OrderBook);
    const eod = [{ trade_date: "2026-10-29", exchange: "BSE", segment: "retail" }] as Trade[];
    expect(liveRowsToShow(live, eod, "2026-10-29")).toEqual([]);
    expect(liveRowsToShow(live, eod, "2026-10-30")).toHaveLength(1);
    expect(
      liveRowsToShow(live, [{ ...eod[0], segment: "otc" }] as Trade[], "2026-10-29"),
    ).toHaveLength(1);
  });

  it("formats today in IST", () => {
    expect(todayIst(new Date("2026-10-29T20:00:00Z"))).toBe("2026-10-30");
    expect(todayIst(new Date("2026-10-29T10:00:00Z"))).toBe("2026-10-29");
  });
});
