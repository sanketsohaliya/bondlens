// Live order-book depth over the Kite ticker bridge (WS /api/kite/depth).
// The server closes the socket when Kite can't serve this bond (not
// connected, unknown listing, token expired mid-session) — the caller
// then just keeps showing the REST snapshot, so a dead stream degrades
// to today's fetch-on-open behavior rather than an error.
import { useEffect, useState } from "react";
import type { OrderBook } from "../api/types";

export function useDepthStream(
  isin: string,
  enabled: boolean,
): { book: OrderBook | null; live: boolean } {
  const [book, setBook] = useState<OrderBook | null>(null);
  const [live, setLive] = useState(false);

  useEffect(() => {
    setBook(null);
    setLive(false);
    if (!enabled) return;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/api/kite/depth/${isin}`);
    ws.onopen = () => setLive(true);
    ws.onmessage = (e) => {
      try {
        setBook(JSON.parse(e.data) as OrderBook);
      } catch {
        // ignore malformed frames
      }
    };
    ws.onclose = () => setLive(false);
    ws.onerror = () => setLive(false);
    return () => ws.close();
  }, [isin, enabled]);

  return { book, live };
}
