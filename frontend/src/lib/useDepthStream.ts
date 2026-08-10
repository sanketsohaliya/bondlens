// Live order-book depth over the Kite ticker bridge (WS /api/kite/depth).
// The server closes the socket when Kite can't serve this bond (not
// connected, unknown listing, token expired mid-session) — the caller
// then just keeps showing the REST snapshot, so a dead stream degrades
// to today's fetch-on-open behavior rather than an error.
import { useEffect, useState } from "react";
import type { OrderBook } from "../api/types";

export type StreamStatus =
  | "idle" // disabled (Kite not connected)
  | "connecting" // socket created, handshake in flight
  | "live" // socket open — streaming, even if no tick has arrived yet
  | "closed"; // errored or closed by the server/network

export function useDepthStream(
  isin: string,
  enabled: boolean,
): { book: OrderBook | null; status: StreamStatus } {
  const [book, setBook] = useState<OrderBook | null>(null);
  const [status, setStatus] = useState<StreamStatus>("idle");

  useEffect(() => {
    setBook(null);
    if (!enabled) {
      setStatus("idle");
      return;
    }
    setStatus("connecting");
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/api/kite/depth/${isin}`);
    ws.onopen = () => setStatus("live");
    ws.onmessage = (e) => {
      try {
        setBook(JSON.parse(e.data) as OrderBook);
      } catch {
        // ignore malformed frames
      }
    };
    ws.onclose = () => setStatus("closed");
    ws.onerror = () => setStatus("closed");
    return () => {
      // Detach before closing so unmount doesn't set state.
      ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
      ws.close();
    };
  }, [isin, enabled]);

  return { book, status };
}
