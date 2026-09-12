import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { BondDetail, OrderBook } from "../api/types";
import { buildBondPrompt } from "../lib/aiPrompt";
import { runAskAi } from "../lib/askAi";
import { CHATGPT_URL, LINK_TIPS } from "../lib/links";

type Phase = "idle" | "working" | "copied" | "blocked" | "failed";

/** One control for "get this bond into an AI": opens ChatGPT in a new tab
 * synchronously (popup-safe), fetches the bond detail if the caller did not
 * already have it, builds the full evidence prompt and copies it. Shared by
 * the Explore row (compact, hover-revealed) and the drawer header (pill). */
export default function AiPromptBtn({
  isin,
  bond,
  variant,
}: {
  isin: string;
  bond?: BondDetail;
  variant: "row" | "drawer";
}) {
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<Phase>("idle");
  const [manual, setManual] = useState("");

  const label =
    phase === "working"
      ? "…"
      : phase === "copied"
        ? "✓ Copied"
        : phase === "blocked"
          ? "✓ Copied — open chatgpt.com"
          : "Ask AI ↗";
  // Row buttons sit behind a hover reveal; warming the detail query then means
  // the click itself has no await, so copy and open both run inside activation.
  const prefetch = () => {
    if (bond) return;
    void queryClient.prefetchQuery({
      queryKey: ["bond", isin, undefined],
      queryFn: () => api.bond(isin, undefined),
      staleTime: 60_000,
    });
  };
  const tip =
    phase === "blocked"
      ? LINK_TIPS.askAiBlocked
      : phase === "failed"
        ? LINK_TIPS.askAiFailed
        : LINK_TIPS.askAi;

  const settle = (next: Phase) => {
    setPhase(next);
    setTimeout(() => setPhase("idle"), 2000);
  };

  const onClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setManual("");
    setPhase("working");
    const result = await runAskAi<BondDetail>({
      open: () => window.open(CHATGPT_URL, "_blank", "noopener,noreferrer"),
      loadDetail: () =>
        bond
          ? Promise.resolve(bond)
          : queryClient.fetchQuery({
              queryKey: ["bond", isin, undefined],
              queryFn: () => api.bond(isin, undefined),
              staleTime: 60_000,
            }),
      buildPrompt: (detail) =>
        buildBondPrompt(detail, {
          depth: queryClient.getQueryData<OrderBook>(["depth", isin]) ?? null,
        }),
      copy: (prompt) => navigator.clipboard.writeText(prompt),
    });
    if (result.status === "copied") settle(result.opened ? "copied" : "blocked");
    else if (result.status === "fetch-failed") settle("failed");
    else {
      setManual(result.prompt);
      setPhase("idle");
    }
  };

  const className =
    variant === "row"
      ? `text-[10px] whitespace-nowrap ${phase === "copied" || phase === "blocked" ? "text-emerald-500" : "text-zinc-600 hover:text-emerald-400"} transition`
      : `text-xs px-3 py-1.5 rounded-md border whitespace-nowrap ${phase === "copied" || phase === "blocked" ? "border-emerald-600/50 text-emerald-400" : "border-zinc-700 text-zinc-300 hover:border-zinc-500 hover:text-zinc-100"} transition-colors`;

  return (
    <span className="relative inline-flex items-center">
      <button
        type="button"
        onClick={onClick}
        onMouseEnter={prefetch}
        onFocus={prefetch}
        disabled={phase === "working"}
        title={tip}
        aria-label={tip}
        aria-busy={phase === "working"}
        className={className}
      >
        {label}
      </button>
      {manual && (
        <div
          className={`absolute top-full z-50 mt-2 w-80 rounded border border-amber-500/40 bg-zinc-950 p-3 text-xs text-amber-300 ${variant === "row" ? "left-0" : "right-0"}`}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
          role="presentation"
        >
          Clipboard access failed. Select and copy the prompt below.
          <textarea
            readOnly
            value={manual}
            aria-label="Complete AI review prompt"
            className="mt-2 h-40 w-full bg-zinc-900 text-zinc-300"
            onFocus={(event) => event.currentTarget.select()}
          />
          <button type="button" className="mt-1" onClick={() => setManual("")}>
            Close
          </button>
        </div>
      )}
    </span>
  );
}
