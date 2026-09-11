import { useEffect, useState } from "react";
import { fmt } from "../api/client";
import type { AskYtm, Bond } from "../api/types";
import { calendarAsOf, calendarDate } from "./cashflowEvidence";

type PayoutData = Pick<Bond, "payout_total_per_unit" | "payout_total_state" | "payout_as_of">;

export function payoutDisplay(bond: PayoutData, today: string) {
  let state = bond.payout_total_state ?? "unavailable";
  if (bond.payout_as_of && bond.payout_as_of !== today) state = "stale";
  else if (!bond.payout_as_of) state = "unavailable";
  const amount = bond.payout_total_per_unit;
  if (
    ["known", "estimated"].includes(state) &&
    (amount == null || !Number.isFinite(amount) || amount < 0)
  )
    state = "incomplete";
  const usable = state === "known" || state === "estimated";
  const label = usable ? `${state === "estimated" ? "~" : ""}${fmt.inr(amount)}/u` : "–";
  const reason = {
    known: "Remaining gross modeled interest plus principal per unit for a new buyer.",
    estimated: "Indicative total: includes estimated cashflows or variable-rate assumptions.",
    incomplete:
      "A complete bounded payout total is unavailable; no known subtotal is shown as a total.",
    open_ended: "No finite lifetime payout total for a perpetual or open-ended schedule.",
    unavailable: "Payout summary is not available yet. Open Cashflows for the schedule.",
    stale:
      "Payout snapshot is out of date; amount and remaining-count fraction are withheld until refreshed.",
  }[state];
  const details = [reason];
  if (usable && amount != null)
    details.push(
      `Total ₹${fmt.num(amount, 2)}/unit, before taxes/fees. Not profit, present value or guaranteed receipt.`,
    );
  if (bond.payout_as_of)
    details.push(
      `Snapshot date: ${calendarDate(bond.payout_as_of)}. Based on the NSDL-derived buyer-entitlement model.`,
    );
  return { label, detail: details.join(" "), state, showCount: state !== "stale" };
}

export function askTicket(ask: AskYtm | null | undefined): number | null {
  if (
    !ask ||
    !Number.isFinite(ask.ask_price) ||
    ask.ask_price <= 0 ||
    ask.ask_qty == null ||
    !Number.isFinite(ask.ask_qty) ||
    ask.ask_qty <= 0
  )
    return null;
  const total = ask.ask_price * ask.ask_qty;
  return Number.isFinite(total) && total > 0 ? total : null;
}

export function askTicketText(ask: AskYtm | null | undefined) {
  const total = askTicket(ask);
  if (total == null) {
    const price =
      ask && Number.isFinite(ask.ask_price) && ask.ask_price > 0
        ? `Displayed price ₹${fmt.num(ask.ask_price, 2)}; `
        : "";
    return {
      label: ask ? "Size unavailable" : "–",
      detail: `${price}displayed best-ask ticket size is unavailable. Open Order Book to inspect the quote.`,
    };
  }
  return {
    label: `${fmt.inr(total)} ticket`,
    detail: `Displayed best-ask snapshot: ₹${fmt.num(ask?.ask_price, 2)} × ${fmt.num(ask?.ask_qty, 2)} units = ₹${fmt.num(total, 2)}. Entire selected best-price level, not a minimum purchase. Excludes brokerage/charges; availability can change.`,
  };
}

export function watchPayoutDay(onDay: (day: string) => void): () => void {
  let timer: ReturnType<typeof setTimeout>;
  let stopped = false;
  const update = () => {
    if (stopped) return;
    clearTimeout(timer);
    const now = new Date();
    const day = calendarAsOf(undefined, now);
    onDay(day);
    const nextMidnight = new Date(`${day}T00:00:00+05:30`).getTime() + 86_400_000;
    timer = setTimeout(update, Math.max(1000, nextMidnight - now.getTime() + 25));
  };
  const visible = () => {
    if (!document.hidden) update();
  };
  update();
  window.addEventListener("focus", update);
  document.addEventListener("visibilitychange", visible);
  return () => {
    stopped = true;
    clearTimeout(timer);
    window.removeEventListener("focus", update);
    document.removeEventListener("visibilitychange", visible);
  };
}

export function usePayoutDay(): string {
  const [day, setDay] = useState(() => calendarAsOf(undefined));
  useEffect(() => watchPayoutDay(setDay), []);
  return day;
}
