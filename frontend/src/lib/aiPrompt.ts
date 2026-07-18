import { fmt } from "../api/client";
import type { BondDetail, RatingEvent } from "../api/types";

/* Builds a self-contained prompt for an external AI (ChatGPT/Gemini/Claude)
   from the drawer's already-loaded BondDetail. Null fields are omitted and
   long lists capped so the prompt stays paste-friendly (~3.5k chars). */

const MAX_RATING_EVENTS = 10;
const MAX_REDEMPTIONS = 12;
const MAX_SIBLINGS = 8;

const INSTRUCTIONS = `You are a fixed-income analyst covering Indian corporate bonds. I'm a retail
investor considering the bond below. All amounts are ₹ per unit.

Do two things:
1. INVESTMENT BRIEF — what this bond is in plain English, whether the yield
   compensates for the risk, and what kind of investor it suits.
2. RISK AUDIT — everything that could go wrong: credit history, default
   records, liquidity, structural quirks, and any data that looks off.

Also search the web for recent news on the issuer (results, rating actions,
defaults, promoter/governance issues) and factor that in.

Be direct and specific. End with a one-line verdict.`;

/** Loose yes/no from raw bond_terms values ("Yes"/"NO"/true/…). */
function yn(v: unknown): string | null {
  if (v == null || v === "") return null;
  if (typeof v === "boolean") return v ? "yes" : "no";
  const s = String(v).trim().toLowerCase();
  if (["yes", "y", "true", "1"].includes(s)) return "yes";
  if (["no", "n", "false", "0"].includes(s)) return "no";
  return null;
}

function listings(b: BondDetail): string | null {
  const parts: string[] = [];
  if (b.on_nse) parts.push(b.nse_symbol ? `NSE (${b.nse_symbol})` : "NSE");
  if (b.on_bse) parts.push(b.bse_scrip_id ? `BSE (${b.bse_scrip_id})` : "BSE");
  return parts.length ? parts.join(", ") : null;
}

function ytmLine(b: BondDetail): string | null {
  if (b.ytm == null) return null;
  const method =
    b.ytm_exact != null
      ? b.amounts_estimated
        ? "from cashflows, some amounts estimated"
        : "exact, from published cashflows"
      : "approximation";
  return `YTM: ${fmt.pct(b.ytm)} (${method})`;
}

function ratingEvents(b: BondDetail): string[] {
  const events: (RatingEvent & { agency: string })[] = [];
  for (const [agency, evs] of Object.entries(b.rating_timeline ?? {})) {
    for (const e of evs) if (e.rating) events.push({ ...e, agency });
  }
  events.sort((a, z) => (z.rating_date ?? "").localeCompare(a.rating_date ?? ""));
  return events
    .slice(0, MAX_RATING_EVENTS)
    .map(
      (e) =>
        `${e.agency} ${e.rating}${e.outlook ? ` (${e.outlook})` : ""}` +
        `${e.action ? ` ${e.action}` : ""}${e.rating_date ? ` ${fmt.date(e.rating_date)}` : ""}`,
    );
}

function cashflowLines(b: BondDetail): string[] {
  const out: string[] = [];
  if (b.next_payment?.amount) {
    out.push(
      `Next payment: ₹${b.next_payment.amount.toLocaleString("en-IN")} ` +
        `${b.next_payment.event} on ${fmt.date(b.next_payment.due_date)}`,
    );
  }
  const future = (b.cashflows ?? []).filter((f) => f.future && f.amount);
  const nInterest = future.filter((f) => f.event === "interest").length;
  const redemptions = future.filter((f) => f.event === "redemption");
  const redTxt = redemptions
    .slice(0, MAX_REDEMPTIONS)
    .map((f) => `₹${(f.amount as number).toLocaleString("en-IN")} on ${fmt.date(f.due_date)}`)
    .join(", ");
  const more =
    redemptions.length > MAX_REDEMPTIONS ? ` (+${redemptions.length - MAX_REDEMPTIONS} more)` : "";
  if (future.length) {
    out.push(
      `Future: ${nInterest} interest payment${nInterest === 1 ? "" : "s"}` +
        (redTxt ? `; redemptions: ${redTxt}${more}` : "; no redemption rows"),
    );
  }
  if (b.future_total_per_unit) {
    out.push(`Total future receipts: ₹${b.future_total_per_unit.toLocaleString("en-IN")}/unit`);
  }
  if (b.amounts_estimated) out.push("Note: some cashflow amounts are estimated, not published.");
  return out;
}

function siblingLines(b: BondDetail): string[] {
  return (b.issuer_bonds ?? []).slice(0, MAX_SIBLINGS).map((s) => {
    const bits = [
      s.coupon_rate != null ? `${fmt.pct(s.coupon_rate)}` : null,
      s.redemption_date ? `matures ${fmt.date(s.redemption_date)}` : null,
      s.close != null ? `last ₹${s.close.toLocaleString("en-IN")}` : "untraded",
    ].filter(Boolean);
    return `${s.isin}: ${bits.join(", ")}`;
  });
}

export function buildBondPrompt(b: BondDetail): string {
  const t = (b.terms ?? {}) as Record<string, unknown>;
  const secured = yn(t.secured);
  const taxFree = yn(t.tax_free);
  const perpetual = yn(t.perpetual) === "yes" || (!b.maturity_date && !!t.perpetual);

  const sections: [string | null, (string | null)[]][] = [
    [
      null,
      [
        `ISIN: ${b.isin}`,
        `Issuer: ${b.issuer_name ?? "unknown"}${b.sector ? ` (${b.sector})` : ""}`,
        b.industry && b.industry !== b.sector ? `Industry: ${b.industry}` : null,
        listings(b) ? `Listed: ${listings(b)}` : null,
      ],
    ],
    [
      "Terms",
      [
        b.coupon != null
          ? `Coupon: ${fmt.pct(b.coupon)}${b.frequency ? ` ${b.frequency}` : ""}` +
            (b.face_value != null ? `, face value ₹${b.face_value.toLocaleString("en-IN")}` : "")
          : b.face_value != null
            ? `Face value: ₹${b.face_value.toLocaleString("en-IN")}`
            : null,
        [
          secured ? `Secured: ${secured}` : null,
          taxFree ? `Tax-free: ${taxFree}` : null,
          perpetual ? "Perpetual: yes" : null,
        ]
          .filter(Boolean)
          .join(" | ") || null,
        b.maturity_date
          ? `Matures: ${fmt.date(b.maturity_date)}` +
            (b.years_to_maturity != null ? ` (${fmt.yearsMonths(b.years_to_maturity)})` : "")
          : null,
        b.call_dates?.length
          ? `Call dates: ${b.call_dates.map((d) => fmt.date(d)).join(", ")}`
          : null,
        `Schedule shape: ${b.schedule_shape}`,
        b.schedule_warnings?.length ? `Schedule warnings: ${b.schedule_warnings.join("; ")}` : null,
      ],
    ],
    [
      b.trade_date
        ? `Pricing (as of ${fmt.date(b.trade_date)}${b.exchange ? `, ${b.exchange}` : ""})`
        : "Pricing",
      [
        b.custom_price ? "NOTE: price below is a hypothetical what-if, not a market trade." : null,
        b.ltp != null
          ? `Price (dirty): ₹${b.ltp.toLocaleString("en-IN")}` +
            (b.clean_price != null ? ` | Clean: ₹${b.clean_price.toLocaleString("en-IN")}` : "") +
            (b.accrued != null ? ` | Accrued: ₹${b.accrued.toLocaleString("en-IN")}` : "")
          : "Never traded on-exchange (no price available).",
        ytmLine(b),
        b.yield_to_worst != null && b.yield_to_worst !== b.ytm
          ? `Yield to worst: ${fmt.pct(b.yield_to_worst)}`
          : null,
        b.current_yield != null ? `Current yield: ${fmt.pct(b.current_yield)}` : null,
        b.duration != null ? `Duration: ${fmt.yearsMonths(b.duration)}` : null,
        b.days_since_traded != null ? `Days since last trade: ${b.days_since_traded}` : null,
        b.otc_price_pct != null
          ? `OTC institutional benchmark: ${fmt.pct(b.otc_price_pct)} of face` +
            (b.otc_yield != null ? ` @ ${fmt.pct(b.otc_yield)}` : "") +
            (b.otc_trade_date ? ` (${fmt.date(b.otc_trade_date)})` : "")
          : null,
      ],
    ],
    [
      "Credit",
      [
        b.credit_rating ? `Rating: ${b.credit_rating}` : "Rating: unrated / none on record",
        ratingEvents(b).length ? `Rating history: ${ratingEvents(b).join(" · ")}` : null,
        `Defaults on record: ${b.defaults?.length || "none"}` +
          (b.technical_defaults?.length
            ? ` | Technical defaults: ${b.technical_defaults.length}`
            : "") +
          ` | In default now: ${b.in_default ? "YES" : "no"}`,
      ],
    ],
    ["Cashflows (summary)", cashflowLines(b)],
    [siblingLines(b).length ? "Same issuer" : null, siblingLines(b)],
  ];

  const body = sections
    .filter(([, lines]) => lines.some(Boolean))
    .map(([title, lines]) => {
      const txt = lines.filter(Boolean).join("\n");
      return title === null ? txt : `-- ${title} --\n${txt}`;
    })
    .join("\n\n");

  return `${INSTRUCTIONS}\n\n=== BOND DATA ===\n${body}\n=== END DATA ===`;
}
