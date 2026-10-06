import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { Notification } from "../api/types";
import AlertsPanel from "./AlertsPanel";

/** Kind → glyph for the feed rows. */
function kindIcon(n: Notification): { glyph: string; className: string } {
  switch (n.kind) {
    case "rating":
      return n.severity === "warn"
        ? { glyph: "▼", className: "text-red-400" }
        : { glyph: "▲", className: "text-emerald-400" };
    case "rating_watch":
    case "rating_outlook":
    case "rating_withdrawn":
      return { glyph: "R", className: n.severity === "warn" ? "text-amber-400" : "text-zinc-400" };
    case "order_import_failed":
      return { glyph: "!", className: "text-amber-400" };
    case "order_filled":
      return { glyph: "B", className: "text-sky-400" };
    case "pay_confirmed":
      return { glyph: "✓", className: "text-emerald-400" };
    case "pay_overdue":
    case "default":
      return {
        glyph: "⚠",
        className: n.severity === "critical" ? "text-red-400" : "text-amber-400",
      };
    case "anomaly":
      return { glyph: "≈", className: n.severity === "warn" ? "text-amber-400" : "text-zinc-400" };
    case "ask_opportunity":
      return { glyph: "▲", className: "text-emerald-400" };
    case "bid_opportunity":
      return { glyph: "▼", className: "text-sky-400" };
    default: // pay_upcoming / pay_today
      return { glyph: "₹", className: "text-sky-400" };
  }
}

function severityBar(severity: string): string {
  if (severity === "critical") return "bg-red-500";
  if (severity === "warn") return "bg-amber-500";
  return "bg-emerald-500";
}

function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(ms / 60_000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d`;
  return new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

const OPPORTUNITY_KINDS = new Set(["ask_opportunity", "bid_opportunity"]);

export default function NotificationsBell({
  onSelect,
}: {
  onSelect: (isin: string, tab?: "order book") => void;
}) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<"feed" | "alerts">("feed");
  // Ids that were unread when the panel opened stay highlighted until close.
  const [freshIds, setFreshIds] = useState<Set<number>>(new Set());
  const queryClient = useQueryClient();

  const feed = useInfiniteQuery({
    queryKey: ["notifications"],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => api.notifications.get(50, pageParam),
    getNextPageParam: (last, pages) => (last.items.length === 50 ? pages.length * 50 : undefined),
    refetchInterval: 60_000,
  });

  const markRead = useMutation({
    mutationFn: api.notifications.markRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });

  const items = [
    ...new Map(
      (feed.data?.pages.flatMap((page) => page.items) ?? []).map((item) => [item.id, item]),
    ).values(),
  ];
  const unread = feed.data?.pages[0]?.unread ?? 0;
  const hasAlert = items.some((n) => n.read_at === null && n.severity !== "info");

  const toggle = () => {
    if (!open) {
      setFreshIds(new Set(items.filter((n) => n.read_at === null).map((n) => n.id)));
    } else {
      setFreshIds(new Set());
    }
    setOpen(!open);
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={toggle}
        title="Notifications"
        className="relative flex items-center justify-center w-8 h-8 rounded-full border border-zinc-700 hover:border-zinc-400 transition-colors text-zinc-400 hover:text-zinc-200"
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
          <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
        </svg>
        {unread > 0 && (
          <span
            className={`absolute -top-1 -right-1 min-w-4 h-4 px-1 rounded-full text-[10px] font-bold leading-4 text-center text-zinc-950 ${
              hasAlert ? "bg-red-400" : "bg-emerald-400"
            }`}
          >
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={toggle} aria-hidden="true" />
          <div className="absolute right-0 top-full mt-2 z-30 w-96 rounded-lg border border-zinc-700 bg-zinc-900 shadow-xl overflow-hidden">
            <div className="flex items-center gap-3 px-3 py-2 border-b border-zinc-800 text-xs font-bold uppercase tracking-wider">
              <button
                type="button"
                onClick={() => setView("feed")}
                className={view === "feed" ? "text-zinc-200" : "text-zinc-500 hover:text-zinc-300"}
              >
                Notifications
              </button>
              <button
                type="button"
                onClick={() => setView("alerts")}
                className={
                  view === "alerts" ? "text-zinc-200" : "text-zinc-500 hover:text-zinc-300"
                }
                title="Opportunity alert rules, push topic and recent hits"
              >
                Alerts
              </button>
              {view === "feed" && (
                <button
                  type="button"
                  className="ml-auto font-normal normal-case text-zinc-400 hover:text-zinc-200"
                  disabled={markRead.isPending || !items.some((item) => item.read_at === null)}
                  onClick={() =>
                    markRead.mutate(
                      items.filter((item) => item.read_at === null).map((item) => item.id),
                    )
                  }
                >
                  Mark loaded as read
                </button>
              )}
            </div>
            {view === "alerts" && (
              <AlertsPanel
                onSelect={(isin, tab) => {
                  onSelect(isin, tab);
                  toggle();
                }}
              />
            )}
            {view === "feed" && (
              <>
                {feed.isError && (
                  <div className="p-3 text-xs text-amber-400">
                    Could not refresh notifications.{" "}
                    <button type="button" onClick={() => feed.refetch()} className="underline">
                      Retry
                    </button>
                  </div>
                )}
                {markRead.isError && (
                  <div className="p-3 text-xs text-amber-400">
                    Read status could not be saved. Please retry.
                  </div>
                )}
                {feed.isPending && (
                  <div className="p-3 text-xs text-zinc-500">Loading notifications…</div>
                )}
                <div className="max-h-96 overflow-y-auto divide-y divide-zinc-800/60">
                  {items.length === 0 && !feed.isPending && !feed.isError && (
                    <div className="px-3 py-6 text-center text-xs text-zinc-500">
                      No notifications yet
                    </div>
                  )}
                  {items.map((n) => {
                    const icon = kindIcon(n);
                    const fresh = freshIds.has(n.id) || n.read_at === null;
                    return (
                      <button
                        key={n.id}
                        type="button"
                        onClick={() => {
                          if (n.read_at === null) markRead.mutate([n.id]);
                          onSelect(
                            n.isin,
                            OPPORTUNITY_KINDS.has(n.kind) ? "order book" : undefined,
                          );
                          toggle();
                        }}
                        className="relative w-full flex items-start gap-2.5 px-3 py-2.5 text-left hover:bg-zinc-800/60 transition-colors"
                      >
                        {fresh && (
                          <span
                            className={`absolute left-0 top-0 bottom-0 w-0.5 ${severityBar(n.severity)}`}
                          />
                        )}
                        <span
                          className={`mt-px w-4 text-center text-sm shrink-0 ${icon.className}`}
                        >
                          {icon.glyph}
                        </span>
                        <span className="flex-1 min-w-0">
                          {n.legacy && (
                            <span className="block text-[11px] text-amber-400">
                              Legacy payment alert — verify against the current source and bank
                              credit.
                            </span>
                          )}
                          <span className="block text-xs text-zinc-200 leading-snug">
                            {n.title}
                          </span>
                          {n.body && (
                            <span className="block mt-0.5 text-[11px] text-zinc-500 leading-snug">
                              {n.body}
                            </span>
                          )}
                        </span>
                        <span className="text-[10px] text-zinc-600 shrink-0 mt-px">
                          {relativeTime(n.created_at)}
                        </span>
                      </button>
                    );
                  })}
                  {feed.hasNextPage && (
                    <button
                      type="button"
                      disabled={feed.isFetchingNextPage}
                      onClick={() => feed.fetchNextPage()}
                      className="w-full p-3 text-xs text-zinc-400 hover:text-zinc-200"
                    >
                      {feed.isFetchingNextPage ? "Loading…" : "Load older notifications"}
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}
