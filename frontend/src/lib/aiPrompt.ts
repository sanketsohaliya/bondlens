import { fmt } from "../api/client";
import type { BondDetail, OrderBook, RatingEvent } from "../api/types";

/* Builds a self-contained prompt for an external AI (ChatGPT/Gemini/Claude)
   from the drawer's already-loaded BondDetail (plus the cached order-book
   snapshot when available). Null fields are omitted and long lists capped
   so the prompt stays paste-friendly (≤ ~7k chars — inside chatgpt.com's
   ?q= URL limit; the clipboard copy is identical). The full dated cashflow
   schedule + today's date are included so the AI can independently verify
   our YTM. */

const MAX_RATING_EVENTS = 10;
const MAX_FLOWS = 40;
const MAX_SIBLINGS = 8;
const MAX_LINKS = 5;

const INSTRUCTIONS = `You are a fixed-income analyst covering Indian corporate bonds. I'm a retail
investor considering the bond below. All amounts are ₹ per unit.

Do three things:
1. INVESTMENT BRIEF — what this bond is in plain English, whether the yield
   compensates for the risk, and what kind of investor it suits. Compare the
   yield against a similar-maturity Indian G-sec (search current levels) and
   say whether the credit spread compensates. Frame returns post-tax: coupon
   interest is taxed at slab (assume 30%) unless the bond is tax-free.
2. RISK AUDIT — everything that could go wrong: credit history, default
   records, liquidity, structural quirks, and any data that looks off.
3. VERIFY — using today's date, the dirty price and the cashflow schedule
   below, independently recompute the YTM (and check accrued interest
   against the day-count convention). If your YTM differs from the stated
   one by more than 0.10 percentage points, flag it and show your working.

Also search the web for recent news on the issuer (results, rating actions,
defaults, promoter/governance issues) and factor that in. If rating-rationale
or offer-document links are provided below, read them too. If a fact is not
in the data and you can't verify it, say "unknown" — do not invent numbers.

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

/** Non-empty trimmed string or null. */
function str(v: unknown): string | null {
  if (v == null) return null;
  const s = String(v).trim();
  return s && s !== "-" && s !== "--" ? s : null;
}

function listings(b: BondDetail): string | null {
  const parts: string[] = [];
  if (b.on_nse) parts.push(b.nse_symbol ? `NSE (${b.nse_symbol})` : "NSE");
  if (b.on_bse) parts.push(b.bse_scrip_id ? `BSE (${b.bse_scrip_id})` : "BSE");
  return parts.length ? parts.join(", ") : null;
}

function couponLine(b: BondDetail, t: Record<string, unknown>): string | null {
  const type = str(t.coupon_type);
  const floating = type != null && /float/i.test(type);
  const basis = str(t.coupon_basis);
  if (b.coupon != null) {
    return (
      `Coupon: ${fmt.pct(b.coupon)}${b.frequency ? ` ${b.frequency}` : ""}` +
      (floating ? ` — FLOATING RATE (resets${basis ? `, basis: ${basis}` : ""})` : "") +
      (b.face_value != null ? `, face value ₹${b.face_value.toLocaleString("en-IN")}` : "")
    );
  }
  if (b.implied_coupon != null) {
    return (
      `Coupon: none periodic — pays everything at maturity (${b.schedule_shape}); ` +
      `≈${fmt.pct(b.implied_coupon)} effective annual implied by the cashflows ` +
      `(issuer may quote it under a different compounding convention)` +
      (b.face_value != null ? `. Face value ₹${b.face_value.toLocaleString("en-IN")}` : "")
    );
  }
  return b.face_value != null ? `Face value: ₹${b.face_value.toLocaleString("en-IN")}` : null;
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

function liquidityLine(b: BondDetail): string | null {
  if (b.turnover == null && b.num_trades == null && b.volume == null) return null;
  const bits = [
    b.turnover != null ? `turnover ${fmt.inr(b.turnover)}` : null,
    b.num_trades != null ? `${b.num_trades} trade${b.num_trades === 1 ? "" : "s"}` : null,
    b.volume != null ? `${b.volume.toLocaleString("en-IN")} units` : null,
  ].filter(Boolean);
  return `Liquidity (last traded day): ${bits.join(" · ")}`;
}

function depthLines(depth: OrderBook | null | undefined): string[] {
  if (!depth) return [];
  const out: string[] = [];
  for (const [name, side] of [
    ["BSE", depth.bse],
    ["NSE", depth.nse],
  ] as const) {
    if (!side) continue;
    const bid = side.bids[0];
    const ask = side.asks[0];
    if (!bid?.price && !ask?.price) continue;
    const fmtLevel = (l: { price: number | null; qty: number | null } | undefined) =>
      l?.price != null ? `₹${l.price.toLocaleString("en-IN")} × ${l.qty ?? "?"}` : "none";
    const ytm = ask?.price != null ? depth.ytm_at_ask.find((y) => y.price === ask.price) : null;
    out.push(
      `Order book now (${name}): best bid ${fmtLevel(bid)}, best ask ${fmtLevel(ask)}` +
        ` (total bid ${side.total_bid_qty ?? "?"} / ask ${side.total_ask_qty ?? "?"})` +
        (ytm?.ytm != null ? ` — YTM at best ask ${fmt.pct(ytm.ytm)}` : ""),
    );
  }
  return out;
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

function reportLinks(b: BondDetail): string[] {
  const out: string[] = [];
  const releases: { agency: string; label: string; url: string; date: string }[] = [];
  for (const [agency, evs] of Object.entries(b.rating_timeline ?? {})) {
    for (const e of evs) {
      if (e.press_release) {
        releases.push({
          agency,
          label: e.rating ?? "",
          url: e.press_release,
          date: e.rating_date ?? "",
        });
      }
    }
  }
  releases.sort((a, z) => z.date.localeCompare(a.date));
  for (const r of releases.slice(0, MAX_LINKS)) {
    out.push(
      `Rating rationale: ${r.agency} ${r.label}${r.date ? ` ${fmt.date(r.date)}` : ""} — ${r.url}`,
    );
  }
  for (const d of (b.documents ?? []).filter((d) => d.download_link).slice(0, MAX_LINKS)) {
    out.push(
      `Document: ${d.doc_name ?? "unnamed"}${d.source ? ` (${d.source})` : ""} — ${d.download_link}`,
    );
  }
  return out;
}

/** Full dated future schedule so the AI can recompute the YTM itself.
 * Caps at MAX_FLOWS rows but always keeps the redemption rows. */
function scheduleLines(b: BondDetail): string[] {
  const future = (b.cashflows ?? []).filter((f) => f.future && f.amount);
  if (!future.length) return [];
  const row = (f: (typeof future)[number]) =>
    `${fmt.date(f.due_date)}  ${f.event}  ${f.estimated ? "≈" : ""}₹${(
      f.amount as number
    ).toLocaleString("en-IN")}`;

  let rows: string[];
  if (future.length <= MAX_FLOWS) {
    rows = future.map(row);
  } else {
    const redemptions = future.filter((f) => f.event === "redemption");
    const keepHead = MAX_FLOWS - redemptions.length - 1;
    const head = future.filter((f) => f.event === "interest").slice(0, Math.max(keepHead, 5));
    const skipped = future.length - head.length - redemptions.length;
    rows = [...head.map(row), `… +${skipped} more interest payments …`, ...redemptions.map(row)];
  }
  const out = rows;
  if (b.future_total_per_unit) {
    out.push(`Total future receipts: ₹${b.future_total_per_unit.toLocaleString("en-IN")}/unit`);
  }
  if (b.amounts_estimated) out.push("Note: amounts marked ≈ are estimated, not published.");
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

export function buildBondPrompt(
  b: BondDetail,
  opts?: { depth?: OrderBook | null; today?: Date },
): string {
  const t = (b.terms ?? {}) as Record<string, unknown>;
  const today = opts?.today ?? new Date();
  const secured = yn(t.secured);
  const taxFree = yn(t.tax_free);
  const perpetual = yn(t.perpetual) === "yes" || (!b.maturity_date && !!t.perpetual);
  const put = yn(t.put_indicator);
  const seniority = str(b.sec_type_desc) ?? str(b.instrument_type);
  const dayCount = str(t.day_count_convention);

  const sections: [string | null, (string | null)[]][] = [
    [
      null,
      [
        `Today: ${today.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}`,
        `ISIN: ${b.isin}`,
        `Issuer: ${b.issuer_name ?? "unknown"}${b.sector ? ` (${b.sector})` : ""}`,
        b.industry && b.industry !== b.sector ? `Industry: ${b.industry}` : null,
        listings(b) ? `Listed: ${listings(b)}` : null,
      ],
    ],
    [
      "Terms",
      [
        couponLine(b, t),
        dayCount ? `Day-count convention: ${dayCount}` : null,
        [
          secured ? `Secured: ${secured}` : null,
          taxFree ? `Tax-free: ${taxFree}` : null,
          perpetual ? "Perpetual: yes" : null,
          put === "yes" ? "Put option: yes" : null,
        ]
          .filter(Boolean)
          .join(" | ") || null,
        seniority ? `Security type: ${seniority}` : null,
        b.trustee_name ? `Trustee: ${b.trustee_name}` : null,
        b.issue_date ? `Issued: ${fmt.date(b.issue_date)}` : null,
        b.maturity_date
          ? `Matures: ${fmt.date(b.maturity_date)}` +
            (b.years_to_maturity != null ? ` (${fmt.yearsMonths(b.years_to_maturity)})` : "")
          : null,
        b.call_dates?.length
          ? `Call dates: ${b.call_dates.map((d) => fmt.date(d)).join(", ")}`
          : null,
        b.further_issues?.length
          ? `Further issues (re-openings): ${b.further_issues.length}`
          : null,
        b.redemption_events?.length
          ? `Partial redemption events on record: ${b.redemption_events.length}`
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
        liquidityLine(b),
        ...depthLines(opts?.depth),
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
    [
      "Cashflow schedule (₹ per unit, future)",
      [
        b.next_payment?.amount
          ? `Next payment: ₹${b.next_payment.amount.toLocaleString("en-IN")} ` +
            `${b.next_payment.event} on ${fmt.date(b.next_payment.due_date)}`
          : null,
        ...scheduleLines(b),
      ],
    ],
    [reportLinks(b).length ? "Reports & documents" : null, reportLinks(b)],
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
