import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { AlertsOverview } from "../api/types";
import NoAutoBuyMark from "./NoAutoBuyMark";

const overview = (blacklist: AlertsOverview["blacklist"]) =>
  ({
    rules: [],
    targets: [],
    settings: { ntfy_topic: null, auto_buy_daily_cap: null },
    recent: [],
    hits_today: {},
    auto_orders: [],
    spent_today: {},
    blacklist,
    watcher: null,
  }) as AlertsOverview;

const render = (blacklist: AlertsOverview["blacklist"]) => {
  const client = new QueryClient();
  client.setQueryData(["alerts"], overview(blacklist));
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <NoAutoBuyMark isin="INE000A01001" />
    </QueryClientProvider>,
  );
};

describe("never-auto-buy mark", () => {
  it("reads the list from the alerts overview and shows the note on hover", () => {
    const off = render([]);
    expect(off).toContain("⊘");
    expect(off).toContain('aria-pressed="false"');
    expect(off).toContain("Never auto-buy this bond (alerts still fire)");
    const on = render([
      {
        isin: "INE000A01001",
        issuer_name: "X",
        note: "thin book",
        created_at: "2026-10-30T00:00:00Z",
      },
    ]);
    expect(on).toContain('aria-pressed="true"');
    expect(on).toContain("Never auto-buy: thin book");
    expect(on).toContain("text-red-400");
  });
});
