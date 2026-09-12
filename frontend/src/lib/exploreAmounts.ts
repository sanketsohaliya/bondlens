import { useEffect, useState } from "react";
import { fmt } from "../api/client";
import type { AskYtm, Bond } from "../api/types";
import { calendarAsOf, calendarDate } from "./cashflowEvidence";

type PayoutData = Pick<
  Bond,
  "payout_total_per_unit" | "payout_total_state" | "payout_as_of" | "payout_valid_until"
>;

/** Mirrors backend `payout_fields`: a snapshot is stale only once a payment or
 * record date has fallen between its `as_of` and today — before that the buyer
 * total is provably unchanged, so an overnight snapshot stays visible across
 * the IST-midnight rollover instead of blanking until the 07:00 refresh. */
export function payoutDisplay(bond: PayoutData, today: string) {
  let state = bond.payout_total_state ?? "unavailable";
  const asOf = bond.payout_as_of;
  const validUntil = bond.payout_valid_until;
  if (!asOf) state = "unavailable";
  else if (asOf < today && validUntil != null && today >= validUntil) state = "stale";
  const amount = bond.payout_total_per_unit;
  if (
    ["known", "estimated"].includes(state) &&
    (amount == null || !Number.isFinite(amount) || amount < 0)
  )
    state = "incomplete";
  const usable = state === "known" || state === "estimated";
  const label = usable ? `${state === "estimated" ? "~" : ""}${fmt.inr(amount)}` : null;
  const reason = {
    known: "Remaining gross modeled interest plus principal per unit for a new buyer.",
    estimated: "Indicative total: includes estimated cashflows or variable-rate assumptions.",
    incomplete:
      "A complete bounded payout total is unavailable; no known subtotal is shown as a total.",
    open_ended: "No finite lifetime payout total for a perpetual or open-ended schedule.",
    unavailable: "Payout summary is not available yet. Open Cashflows for the schedule.",
    stale:
      "Payout updates after the 07:00 IST refresh; a payment or record date has passed since the last snapshot.",
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

type OtcData = Pick<
  Bond,
  "otc_yield" | "otc_price_pct" | "otc_trade_date" | "otc_turnover" | "turnover" | "prev_turnover"
> & { nse_turnover?: number | null; bse_turnover?: number | null };

/** Second line of the Retail turnover cell: the institutional (OTC) yield and
 * clean price to compare against the retail YTM one column left. Amber when
 * the two yields differ by more than OTC_MAX_GAP and the OTC print is recent.
 * Falls back to the OTC turnover when no OTC yield exists. */
export function otcLine(b: OtcData): { text: string; title: string } | null {
  const retail = (b.nse_turnover ?? 0) + (b.bse_turnover ?? 0);
  const title: string[] = [];
  if (b.nse_turnover != null || b.bse_turnover != null) {
    title.push(
      `Retail turnover ${fmt.inr(retail)}${b.prev_turnover != null ? ` (prev ${fmt.inr(b.prev_turnover)})` : ""}.`,
    );
  }
  const hasYield = b.otc_yield != null && Number.isFinite(b.otc_yield);
  const hasTurnover = b.otc_turnover != null;
  if (!hasYield && !hasTurnover) return null;
  // Line text: turnover, then yield @ clean price. Any missing part is omitted.
  const yieldPart = hasYield
    ? `${fmt.pct(b.otc_yield)}${b.otc_price_pct != null ? ` @ ${fmt.num(Number(b.otc_price_pct))}` : ""}`
    : null;
  const text = ["OTC", hasTurnover ? fmt.inr(b.otc_turnover) : null, yieldPart]
    .filter(Boolean)
    .join(" · ")
    .replace("OTC · ", "OTC ");
  const when = b.otc_trade_date ? ` on ${calendarDate(b.otc_trade_date)}` : "";
  if (hasYield) {
    title.push(
      `OTC${hasTurnover ? ` ${fmt.inr(b.otc_turnover)}` : ""}${when}${b.otc_price_pct != null ? ` at ${fmt.num(Number(b.otc_price_pct))}% of face` : ""}, ${fmt.pct(b.otc_yield)} yield.`,
    );
  } else {
    title.push(`OTC turnover ${fmt.inr(b.otc_turnover)}${when}.`);
  }
  return { text, title: title.join(" ") };
}

/** A row click that lands after the user drag-selected text (e.g. the ISIN)
 * must not open the drawer, or "select and copy" is impossible. */
export function shouldOpenRow(selection: string | null | undefined): boolean {
  return !(selection ?? "").trim();
}
