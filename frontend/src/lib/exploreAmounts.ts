import { useEffect, useState } from "react";
import { fmt } from "../api/client";
import type { AskYtm, Bond, YieldValuation } from "../api/types";
import { calendarAsOf, calendarDate } from "./cashflowEvidence";

type PayoutData = Pick<
  Bond,
  "payout_total_per_unit" | "payout_total_state" | "payout_as_of" | "payout_valid_until"
> &
  Partial<
    Pick<
      Bond,
      "coupon" | "coupon_basis" | "frequency" | "schedule_shape" | "future_flows" | "total_flows"
    >
  >;

const SHAPE_NOTE: Record<string, string> = {
  staggered: "principal repaid in parts, not one lump sum at maturity",
  zero_coupon: "bought at a discount, single payment at maturity",
  cumulative: "interest accrues and is paid with principal at maturity",
  perpetual: "no maturity date",
  irregular: "non-standard schedule",
  plain: "principal repaid in full at maturity",
};

/** Plain-English payout structure ("11.6% fixed interest, paid monthly ·
 * principal repaid in parts…") — never leaks raw enum tokens. */
export function payoutStructureNote(
  b: Partial<Pick<Bond, "coupon" | "coupon_basis" | "frequency" | "schedule_shape">>,
): string | null {
  const basis = (b.coupon_basis ?? "").toLowerCase();
  const zero = basis.includes("zero") || b.schedule_shape === "zero_coupon";
  let rate: string | null = null;
  if (zero) rate = "No coupons";
  else if (b.coupon != null) {
    rate = `${fmt.num(Number(b.coupon))}% ${basis.startsWith("variable") ? "floating" : "fixed"} interest`;
    if (b.frequency && b.frequency !== "cumulative") rate += `, paid ${b.frequency.toLowerCase()}`;
  }
  const shape = b.schedule_shape ? SHAPE_NOTE[b.schedule_shape] : null;
  const parts = [rate, shape].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

export function paymentsLeftText(future: number, total: number): string {
  return future === 0
    ? "no payments left to receive"
    : `${future} of ${total} payments still to come`;
}

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
  const showCount = state !== "stale";
  const label = usable ? `${state === "estimated" ? "~" : ""}${fmt.inr(amount)}` : null;
  const horizon = bond.schedule_shape === "perpetual" ? "the first call" : "maturity";
  const left =
    showCount && bond.future_flows != null && bond.total_flows != null && bond.total_flows > 0
      ? ` — ${paymentsLeftText(bond.future_flows, bond.total_flows)}`
      : "";
  const headline = {
    known: `You'd receive ${fmt.inrFull(amount)} per unit if you buy now and hold to ${horizon}${left}.`,
    estimated: `You'd receive about ${fmt.inrFull(amount)} per unit if you buy now and hold to ${horizon}${left}. Some amounts are estimated (floating rate or missing data).`,
    incomplete:
      "Total can't be shown — some payment amounts are missing, so no partial figure is given.",
    open_ended: "No fixed total — this bond has no maturity, so payments continue indefinitely.",
    unavailable: "Payout total not available yet.",
    stale:
      "Total is being recalculated — a payment or record date has passed. Updates after the 07:00 IST refresh.",
  }[state];
  const lines = [headline, payoutStructureNote(bond)];
  if (usable) lines.push("Before tax & fees · gross cash, not profit or a guaranteed return.");
  lines.push(
    bond.payout_as_of
      ? `Schedule per NSDL as of ${calendarDate(bond.payout_as_of)} · click to see every payment.`
      : "Click to see every payment.",
  );
  return { label, detail: lines.filter(Boolean).join("\n"), state, showCount };
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
  const priced = ask != null && Number.isFinite(ask.ask_price) && ask.ask_price > 0;
  if (total == null) {
    const price = priced ? `Displayed price ₹${fmt.num(ask.ask_price, 2)}; ` : "";
    return {
      label: priced ? `${fmt.inrFull(ask.ask_price)} (size unavailable)` : "–",
      detail: `${price}displayed best-ask ticket size is unavailable. Open Order Book to inspect the quote.`,
    };
  }
  // Ask price first (full rupees, like the LTP column), then the size at
  // that level and what taking all of it costs.
  return {
    label: `${fmt.inrFull(ask?.ask_price)} × ${fmt.num(ask?.ask_qty, 0)} = ${fmt.inr(total)}`,
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
  const text = `OTC ${[hasTurnover ? fmt.inr(b.otc_turnover) : null, yieldPart]
    .filter(Boolean)
    .join(" | ")}`;
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

/** Total return to maturity as a plain percentage of price - what you would
 * pocket, not annualised. Null when the schedule is incomplete (the backend
 * withholds holding_period_return rather than guess), so an absent figure
 * means "unknown". Three-digit values drop the decimal; negatives carry a
 * true minus sign and nothing else - no colour, the number speaks. */
export function absReturnText(
  value: Pick<YieldValuation, "holding_period_return" | "yield_type"> | null | undefined,
): { text: string; title: string } | null {
  const v = value?.holding_period_return;
  if (v == null || !Number.isFinite(v)) return null;
  const magnitude = Math.abs(v) >= 100 ? Math.abs(v).toFixed(0) : Math.abs(v).toFixed(1);
  const horizon = value?.yield_type === "ytc" ? "first call" : "maturity";
  return {
    text: `${v < 0 ? "\u2212" : ""}${magnitude}%`,
    title: `Total return if held to ${horizon}, before tax; not annualised.`,
  };
}
