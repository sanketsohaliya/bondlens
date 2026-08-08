import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect, useState } from "react";
import { api, fmt } from "../api/client";
import type { BondDetail } from "../api/types";
import { buildBondPrompt } from "../lib/aiPrompt";
import { replaceNumberWords } from "../lib/numberWords";
import { usePortfolio } from "../lib/portfolio";
import CashflowTable from "./CashflowTable";
import CopyBtn from "./CopyBtn";
import { HoldMark, WatchStar } from "./ExploreTable";
import HoldingPanel from "./HoldingPanel";
import OrderBook from "./OrderBook";
import RatingTimeline from "./RatingTimeline";
import StatBox from "./StatBox";
import Tip from "./Tooltip";
import TradeChart from "./TradeChart";

export type Tab = "overview" | "chart" | "cashflows" | "order book" | "ratings" | "holding";

const DRAWER_WIDTH_KEY = "bl.drawerWidth";
const DEFAULT_DRAWER_WIDTH = 768; // matches the previous max-w-3xl
const MIN_DRAWER_WIDTH = 600;

/** Factual hover text from the NSDL technical-delay records: when, why. */
function techDelayTip(records: Array<{ [key: string]: unknown }>): string {
  const last = records[records.length - 1];
  if (!last) return "A payment was briefly delayed for technical reasons and later paid";
  const due = typeof last.payment_due_date === "string" ? fmt.date(last.payment_due_date) : null;
  const reason = typeof last.failure_reason === "string" ? last.failure_reason : null;
  return [
    `Payment${due ? ` due ${due}` : ""} was briefly delayed and later paid`,
    reason,
    records.length > 1 ? `(${records.length} such delays on record)` : null,
  ]
    .filter(Boolean)
    .join(" — ");
}

export default function BondDrawer({
  isin,
  initialTab,
  onSelect,
  onClose,
}: {
  isin: string;
  initialTab?: Tab;
  onSelect?: (isin: string) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>(initialTab ?? "overview");
  const [whatIf, setWhatIf] = useState<string>("");
  const [price, setPrice] = useState<number | undefined>(undefined);
  const [width, setWidth] = useState<number>(() => {
    const saved = Number(localStorage.getItem(DRAWER_WIDTH_KEY));
    return Number.isFinite(saved) && saved >= MIN_DRAWER_WIDTH ? saved : DEFAULT_DRAWER_WIDTH;
  });

  const onResizeStart = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    const onMove = (ev: PointerEvent) => {
      const w = Math.min(
        Math.max(window.innerWidth - ev.clientX, MIN_DRAWER_WIDTH),
        window.innerWidth * 0.9,
      );
      setWidth(w);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      setWidth((w) => {
        localStorage.setItem(DRAWER_WIDTH_KEY, String(Math.round(w)));
        return w;
      });
      // Charts inside tabs only re-measure on window resize.
      window.dispatchEvent(new Event("resize"));
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  useEffect(() => {
    const t = setTimeout(() => setPrice(whatIf ? Number(whatIf) : undefined), 400);
    return () => clearTimeout(t);
  }, [whatIf]);

  const { data: bond, isLoading } = useQuery({
    queryKey: ["bond", isin, price],
    queryFn: () => api.bond(isin, price),
    placeholderData: keepPreviousData,
  });

  // The "holding" tab only exists while the bond is in the portfolio;
  // deleting the last lot from inside the tab kicks back to overview.
  const { data: portfolio } = usePortfolio();
  const held = portfolio?.positions.some((p) => p.isin === isin) ?? false;
  const tabs: Tab[] = [
    "overview",
    "chart",
    "cashflows",
    "order book",
    "ratings",
    ...(held ? (["holding"] as Tab[]) : []),
  ];
  useEffect(() => {
    if (tab === "holding" && portfolio && !held) setTab("overview");
  }, [tab, portfolio, held]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <aside
        className="absolute right-0 top-0 h-full max-w-[100vw] bg-zinc-950 border-l border-zinc-800 overflow-y-auto scroll-slim"
        style={{ width }}
      >
        <div
          onPointerDown={onResizeStart}
          className="fixed top-0 h-full w-1.5 -translate-x-0.5 cursor-col-resize z-20 hover:bg-emerald-500/40 active:bg-emerald-500/60 transition-colors"
          style={{ right: Math.min(width, window.innerWidth) - 3 }}
          title="Drag to resize"
        />
        {isLoading || !bond ? (
          <div className="p-8 text-zinc-500 animate-pulse">Loading {isin}…</div>
        ) : (
          <>
            <header className="sticky top-0 bg-zinc-950/95 backdrop-blur border-b border-zinc-800 p-4 z-10">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2.5">
                    <HoldMark isin={bond.isin} />
                    <WatchStar isin={bond.isin} />
                    <h2 className="text-lg font-bold">
                      {bond.nse_symbol ?? bond.bse_scrip_id ?? bond.isin}
                    </h2>
                    {bond.in_default && (
                      <span
                        title="Issuer has missed payments on this bond"
                        className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/15 text-red-400 border border-red-500/30"
                      >
                        DEFAULT
                      </span>
                    )}
                    {bond.tech_delay && !bond.in_default && (
                      <span
                        title={techDelayTip(bond.technical_defaults)}
                        className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 border border-amber-500/30"
                      >
                        TECH DELAY
                      </span>
                    )}
                  </div>
                  <div className="text-sm text-zinc-400 mt-0.5">{bond.issuer_name}</div>
                  <div className="text-xs text-zinc-500 font-mono mt-0.5 flex items-center gap-1.5">
                    {bond.isin}
                    <CopyBtn text={bond.isin} label="ISIN" />
                    {bond.sector && <span>· {bond.sector}</span>}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <AiBriefBtn bond={bond} />
                  <a
                    href={`https://www.indiabondinfo.nsdl.com/CBDServices/#/instrumentDetails/${bond.isin}`}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs px-3 py-1.5 rounded-md border border-zinc-700 text-zinc-300 hover:border-zinc-500 hover:text-zinc-100 transition-colors"
                  >
                    NSDL ↗
                  </a>
                  {bond.kite_url && (
                    <a
                      href={bond.kite_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs px-3 py-1.5 rounded-md border border-zinc-700 text-zinc-300 hover:border-zinc-500 hover:text-zinc-100 transition-colors"
                    >
                      Kite ↗
                    </a>
                  )}
                  <button
                    type="button"
                    onClick={onClose}
                    className="text-zinc-500 hover:text-zinc-200 text-xl leading-none px-2"
                  >
                    ×
                  </button>
                </div>
              </div>
              <nav className="flex gap-1 mt-3 -mb-px">
                {tabs.map((t) => (
                  <button
                    type="button"
                    key={t}
                    onClick={() => setTab(t)}
                    className={
                      "text-sm font-medium px-3.5 py-2 rounded-t-md border-b-2 capitalize transition-colors " +
                      (tab === t
                        ? "border-emerald-500 text-emerald-400"
                        : "border-transparent text-zinc-500 hover:text-zinc-300")
                    }
                  >
                    {t}
                  </button>
                ))}
              </nav>
            </header>

            <div className="p-4 space-y-4">
              {tab === "overview" && (
                <Overview bond={bond} whatIf={whatIf} setWhatIf={setWhatIf} onSelect={onSelect} />
              )}
              {tab === "chart" && <TradeChart isin={bond.isin} faceValue={bond.face_value} />}
              {tab === "cashflows" && <CashflowTable bond={bond} />}
              {tab === "order book" && (
                <OrderBook
                  isin={bond.isin}
                  nseSymbol={bond.nse_symbol}
                  nseSeries={bond.nse_series}
                />
              )}
              {tab === "ratings" && <RatingTimeline timeline={bond.rating_timeline} />}
              {tab === "holding" && <HoldingPanel isin={bond.isin} />}
            </div>
          </>
        )}
      </aside>
    </div>
  );
}

function AiBriefBtn({ bond }: { bond: BondDetail }) {
  const [copied, setCopied] = useState(false);
  return (
    <Tip text="Opens ChatGPT with this bond's analysis prompt (also copied — paste into Gemini/Claude if you prefer)">
      <button
        type="button"
        onClick={() => {
          const prompt = buildBondPrompt(bond);
          navigator.clipboard.writeText(prompt);
          // Synchronous open so popup blockers treat it as user-initiated.
          window.open(
            `https://chatgpt.com/?q=${encodeURIComponent(prompt)}`,
            "_blank",
            "noreferrer",
          );
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
        className={
          "text-xs px-3 py-1.5 rounded-md border transition-colors " +
          (copied
            ? "border-emerald-500/50 text-emerald-400"
            : "border-zinc-700 text-zinc-300 hover:border-zinc-500 hover:text-zinc-100")
        }
      >
        {copied ? "Copied ✓" : "AI brief ↗"}
      </button>
    </Tip>
  );
}

function Stat({
  label,
  value,
  accent,
  tip,
  badge,
}: {
  label: string;
  value: string;
  accent?: boolean;
  tip?: string | null;
  badge?: string;
}) {
  // Long NSDL prose gets clamped to two lines — surface the full text as a
  // tooltip so nothing is lost (explicit tips take precedence). Amounts
  // spelled out in words become numerals.
  const rawTip = tip ?? (value.length > 24 ? value : undefined);
  const shownTip = rawTip ? replaceNumberWords(rawTip) : undefined;
  // Long values claim extra grid columns instead of growing taller — keeps
  // every box the same height. line-clamp stays as a last-resort safety net.
  const span =
    value.length > 60 ? "col-span-2 md:col-span-4" : value.length > 24 ? "col-span-2" : "";
  return (
    <StatBox
      label={label}
      value={value}
      accent={accent}
      badge={badge}
      tip={shownTip}
      className={span}
    />
  );
}

/** Section of stat boxes; renders nothing when every box was omitted. */
function StatSection({
  title,
  action,
  hypothetical,
  children,
}: {
  title: string;
  action?: ReactNode;
  hypothetical?: boolean;
  children: ReactNode;
}) {
  const kids = (Array.isArray(children) ? children : [children]).filter(Boolean);
  if (kids.length === 0) return null;
  return (
    <div>
      <h3 className="flex items-center gap-2 text-xs font-bold text-zinc-200 uppercase tracking-wider mb-2.5">
        <span
          className={`h-3.5 w-1 rounded-full ${hypothetical ? "bg-amber-400/80" : "bg-emerald-500/80"}`}
        />
        {title}
        <span className="flex-1 border-t border-zinc-800" />
        {action && <span className="normal-case font-normal tracking-normal">{action}</span>}
      </h3>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">{children}</div>
    </div>
  );
}

// NSDL terms values arrive as an untyped ragged dict; coerce defensively.
const _TERM_NULLS = new Set([
  "",
  "-",
  "NA",
  "N.A",
  "N.A.",
  "None",
  "Not applicable",
  "NOT APPLICABLE",
  "Not Applicable",
]);
function termStr(terms: BondDetail["terms"], key: string): string | null {
  const v = terms?.[key];
  if (v == null) return null;
  const s = String(v).trim();
  return _TERM_NULLS.has(s) ? null : s;
}
function termNum(terms: BondDetail["terms"], key: string): number | null {
  const v = terms?.[key];
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** NSDL's totalIssueSize has no documented unit; observed values are in
 * rupees for most issues. Values under 1e5 are almost certainly ₹Cr. */
function fmtIssueSize(v: number | null): string | null {
  if (v == null || v <= 0) return null;
  return v < 1e5 ? `₹${fmt.num(v)} Cr` : fmt.inr(v);
}

/** NSDL document names are raw filenames ("CRISIL RATINGS LIMITED_20-02-2026",
 * "Shelf Prospectus/Information Memorandum/Offer Documents/Tranches/Series") —
 * tidy them into short human labels; the full name stays in the title attr. */
function docLabel(name: string | null | undefined): string {
  if (!name) return "document";
  const s = name
    .replace(/_+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[\s·-]+$/, "");
  if (/prospectus|memorandum|offer document/i.test(s)) return "Prospectus / IM";
  // Rating agency press releases: "<AGENCY> <dd-mm-yyyy>" -> "<Agency> PR · <date>"
  const m = s.match(/^(.*?)\s*(\d{2}-\d{2}-\d{4})$/);
  if (m) {
    const agency = m[1]
      .replace(/\b(RATINGS?|LIMITED|LTD\.?|PVT\.?|PRIVATE|AND|RESEARCH)\b/gi, " ")
      .replace(/[\s.]+/g, " ")
      .trim();
    return `${agency || m[1]} PR · ${m[2]}`;
  }
  return s;
}

const EX_INTEREST_TIP =
  "Negative accrued: the record date for the next coupon has passed, so a buyer settling now " +
  "misses that entire coupon — the seller receives it, including the days the buyer will own " +
  "the bond. The buyer is compensated by paying less than the clean price (dirty < clean). " +
  "Standard exchange convention for ex-interest trading.";

const PERIODS: Record<string, [number, string]> = {
  monthly: [12, "monthly"],
  quarterly: [4, "quarterly"],
  semiannual: [2, "half-yearly"],
  annual: [1, "annual"],
};

/** Per-bond explanation of exactly how the shown YTM figure is solved. */
function ytmTip(b: BondDetail): string | null {
  if (b.ytm == null || b.ltp == null) return null;
  const [m, mName] = PERIODS[b.frequency ?? ""] ?? [1, "annual"];
  const future = b.cashflows.filter((f) => f.future && f.amount);
  const total = future.reduce((s, f) => s + (f.amount ?? 0), 0);
  const last = future.length ? fmt.date(future[future.length - 1].due_date) : null;
  if (b.schedule_shape === "perpetual" && b.call_dates.length > 0) {
    return (
      `Perpetual — yield to earliest call (${fmt.date(b.call_dates[0])}). ` +
      `Solves r so that coupons up to the call plus face ${fmt.inr(b.face_value)} at the call, ` +
      `each discounted by (1 + r/${m})^(${m}×t), sum to today's price ${fmt.inr(b.ltp)}. ` +
      `t = years until each payment. r = ${fmt.pct(b.ytm)}.`
    );
  }
  if (b.ytm_exact != null) {
    return (
      `Solved from the actual payment schedule: finds r so that all ${future.length} future ` +
      `payments (${fmt.inr(total)} total, last on ${last}), each discounted by ` +
      `(1 + r/${m})^(${m}×t), sum to the dirty price ${fmt.inr(b.ltp)} paid today. ` +
      `t = years until each payment, compounded ${mName} (m=${m}). r = ${fmt.pct(b.ytm_exact)}.` +
      (b.amounts_estimated ? " Some amounts were estimated as coupon × face." : "")
    );
  }
  return (
    `No usable payment schedule — approximated from headline terms: finds r so that ` +
    `${mName} coupons of ${fmt.pct(b.coupon)} × ${fmt.inr(b.face_value)} ÷ ${m}, plus face ` +
    `${fmt.inr(b.face_value)} at maturity (${fmt.date(b.maturity_date)}), each discounted by ` +
    `(1 + r/${m})^(${m}×t), sum to the price ${fmt.inr(b.ltp)}. r = ${fmt.pct(b.ytm)}.`
  );
}

function Overview({
  bond: b,
  whatIf,
  setWhatIf,
  onSelect,
}: {
  bond: BondDetail;
  whatIf: string;
  setWhatIf: (v: string) => void;
  onSelect?: (isin: string) => void;
}) {
  const t = b.terms;
  const secured = termStr(t, "secured");
  const securedDetails = termStr(t, "secured_details");
  const assetCover = termNum(t, "asset_cover_percent");
  const taxFree = termStr(t, "tax_free");
  const couponType = termStr(t, "coupon_type");
  const couponBasis = termStr(t, "coupon_basis");
  const payFreq = termStr(t, "interest_payment_frequency") ?? b.frequency;
  const freqDesc = termStr(t, "frequency_description");
  const dayCount = termStr(t, "day_count_convention");
  const seniority = termStr(t, "category");
  const secClass = b.sec_type_desc ?? b.instrument_type;
  const issueSize = fmtIssueSize(termNum(t, "total_issue_size"));
  const amountRaised = fmtIssueSize(termNum(t, "amount_raised"));
  const modeOfIssue = termStr(t, "mode_of_issue");
  const hasCall = (termStr(t, "call_indicator") ?? "").toLowerCase() === "yes";
  const hasPut = (termStr(t, "put_indicator") ?? "").toLowerCase() === "yes";
  const instrumentDesc = termStr(t, "instrument_desc");
  const redemptionType = termStr(t, "redemption_type");
  const redemptionNotes = [
    termStr(t, "early_redemption_details"),
    termStr(t, "redemption_premium_details"),
  ]
    .filter(Boolean)
    .join(" · ");
  // Non-plain shapes change what a buyer actually receives — surface them.
  const shapeNote: Record<string, string> = {
    staggered: "Principal repaid in parts over time — not one lump sum at maturity.",
    zero_coupon: "No coupons — issued at a discount, single payment at maturity.",
    cumulative: "Interest accumulates and is paid together with principal at maturity.",
    perpetual: "No maturity date — priced to the earliest call.",
    irregular: "Payment schedule resists standard classification — check Cashflows tab.",
  };
  const listedOn =
    [b.on_nse ? `NSE${b.nse_series ? ` (${b.nse_series})` : ""}` : null, b.on_bse ? "BSE" : null]
      .filter(Boolean)
      .join(" · ") || null;
  const tenorMonths = (termNum(t, "tenure_years") ?? 0) * 12 + (termNum(t, "tenure_months") ?? 0);
  const issuePrice = termNum(t, "issue_price");
  const netQuantity = termNum(t, "net_quantity");

  return (
    <div className="space-y-6">
      <StatSection
        title="Price & Yield"
        hypothetical={!!whatIf}
        action={
          <label className="flex items-center gap-1.5 text-xs text-zinc-400">
            what-if ₹
            <input
              type="number"
              placeholder={b.ltp != null ? String(b.ltp) : "price"}
              value={whatIf}
              onChange={(e) => setWhatIf(e.target.value)}
              className="bg-zinc-900 border border-zinc-700 rounded-md px-2 py-1 text-xs w-28 text-zinc-100 focus:outline-none focus:border-emerald-500"
            />
            {whatIf && (
              <button
                type="button"
                onClick={() => setWhatIf("")}
                className="text-red-400 hover:underline"
              >
                reset
              </button>
            )}
          </label>
        }
      >
        <Stat
          label={`YTM ${b.ytm_exact != null ? "(exact)" : "(approx)"}`}
          value={fmt.pct(b.ytm)}
          accent
          tip={ytmTip(b)}
        />
        {b.yield_to_worst != null && b.yield_to_worst !== b.ytm && (
          <Stat label="Yield to worst" value={fmt.pct(b.yield_to_worst)} />
        )}
        <Stat label="Current yield" value={fmt.pct(b.current_yield)} />
        <Stat label="LTP (dirty)" value={fmt.inrFull(b.ltp)} />
        <Stat label="Clean price" value={fmt.inrFull(b.clean_price)} />
        <Stat
          label="Accrued"
          value={fmt.inrFull(b.accrued)}
          badge={b.accrued != null && b.accrued < 0 ? "ex-interest" : undefined}
          tip={b.accrued != null && b.accrued < 0 ? EX_INTEREST_TIP : undefined}
        />
        {b.otc_price_pct != null && (
          <Stat
            label="OTC (clean %)"
            value={`${fmt.num(b.otc_price_pct)}% @ ${fmt.pct(b.otc_yield)}`}
          />
        )}
        <Stat
          label="Last traded"
          value={b.trade_date ? `${fmt.date(b.trade_date)} (${b.exchange})` : "never"}
        />
      </StatSection>

      <StatSection title="Coupon & Payments">
        <Stat
          label="Coupon"
          value={
            b.coupon == null && b.implied_coupon != null
              ? `≈${fmt.pct(b.implied_coupon)} implied`
              : `${fmt.pct(b.coupon)} ${b.frequency ?? ""}`
          }
          tip={
            b.coupon == null && b.implied_coupon != null
              ? `No published coupon — everything is paid at maturity (${b.schedule_shape}). ` +
                `This is the effective-annual rate implied by the cashflows: face value growing to ` +
                `the total payout between allotment and maturity. The issuer's brochure may quote the ` +
                `same cashflows as a simple or monthly-compounded rate, so treat this as indicative.`
              : undefined
          }
        />
        {payFreq && <Stat label="Payment frequency" value={payFreq} tip={freqDesc} />}
        {couponType && <Stat label="Coupon type" value={couponType} tip={couponBasis} />}
        {dayCount && <Stat label="Day count" value={dayCount} />}
        {(redemptionType || shapeNote[b.schedule_shape]) && (
          <Stat
            label="Redemption"
            value={redemptionType ?? b.schedule_shape.replace("_", " ")}
            badge={shapeNote[b.schedule_shape] ? b.schedule_shape.replace("_", " ") : undefined}
            tip={
              [shapeNote[b.schedule_shape], redemptionNotes].filter(Boolean).join(" ") || undefined
            }
          />
        )}
      </StatSection>

      <StatSection title="Structure & Risk">
        {b.credit_rating && <Stat label="Rating" value={b.credit_rating} />}
        {secured && (
          <Stat
            label="Secured"
            value={secured}
            tip={
              securedDetails || assetCover != null
                ? [securedDetails, assetCover != null ? `Asset cover: ${assetCover}%` : null]
                    .filter(Boolean)
                    .join(" · ")
                : undefined
            }
          />
        )}
        {seniority && <Stat label="Seniority" value={seniority} />}
        {secClass && <Stat label="Security class" value={secClass} tip={instrumentDesc} />}
        {taxFree && (
          <Stat
            label="Tax status"
            value={taxFree.toLowerCase() === "yes" ? "Tax-free" : "Taxable"}
          />
        )}
        {(hasCall || hasPut) && (
          <Stat
            label="Options"
            value={[hasCall ? "Callable" : null, hasPut ? "Puttable" : null]
              .filter(Boolean)
              .join(" · ")}
            tip={b.call_dates.length > 0 ? `Next call: ${fmt.date(b.call_dates[0])}` : undefined}
          />
        )}
      </StatSection>

      <StatSection title="Dates & Tenure">
        <Stat label="Matures" value={fmt.date(b.maturity_date)} />
        {b.years_to_maturity != null && (
          <Stat label="Remaining" value={fmt.yearsMonths(b.years_to_maturity)} />
        )}
        <Stat
          label="Issued"
          value={
            b.issue_date
              ? `${fmt.date(b.issue_date)} (${fmt.yearsMonths(
                  (Date.now() - new Date(b.issue_date).getTime()) / 31557600000,
                )} ago)`
              : "–"
          }
        />
        {b.duration != null && (
          <Stat
            label="Mod. duration"
            value={fmt.num(b.duration)}
            tip="Approximate % price change for a 1% move in yield."
          />
        )}
        {tenorMonths > 0 && (
          <Stat label="Original tenor" value={fmt.yearsMonths(tenorMonths / 12)} />
        )}
      </StatSection>

      <StatSection title="Issue Details">
        <Stat label="Face value" value={fmt.inrFull(b.face_value)} />
        {issueSize && <Stat label="Issue size" value={issueSize} />}
        {amountRaised && amountRaised !== issueSize && (
          <Stat label="Amount raised" value={amountRaised} />
        )}
        {issuePrice != null && issuePrice !== b.face_value && (
          <Stat
            label="Issue price"
            value={fmt.inrFull(issuePrice)}
            tip="Differs from face value — issued at a discount/premium."
          />
        )}
        {netQuantity != null && netQuantity > 0 && (
          <Stat label="Units outstanding" value={fmt.num(netQuantity, 0)} />
        )}
        {listedOn && <Stat label="Listed on" value={listedOn} />}
        {modeOfIssue && <Stat label="Mode of issue" value={modeOfIssue} />}
        {b.trustee_name && <Stat label="Trustee" value={b.trustee_name} tip={b.trustee_name} />}
      </StatSection>

      {b.schedule_warnings.length > 0 && (
        <div className="text-xs text-amber-500/90 bg-amber-500/5 border border-amber-500/20 rounded-lg p-3 space-y-1">
          {b.schedule_warnings.map((w, i) => (
            <div key={i}>⚠ {w}</div>
          ))}
        </div>
      )}

      {b.next_payment && (
        <div className="text-sm text-zinc-300">
          Next payment:{" "}
          <span className="font-semibold text-emerald-400">{fmt.inr(b.next_payment.amount)}</span>{" "}
          <span className="capitalize">({b.next_payment.event})</span> on{" "}
          {fmt.date(b.next_payment.due_date)} · total future{" "}
          <span className="font-semibold">{fmt.inr(b.future_total_per_unit)}</span>/unit
        </div>
      )}

      {b.issuer_bonds.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">
            Same issuer ({b.issuer_bonds.length})
          </h3>
          <div className="rounded-lg border border-zinc-800 divide-y divide-zinc-800/60 max-h-56 overflow-y-auto scroll-slim">
            {b.issuer_bonds.map((s) => (
              <button
                type="button"
                key={s.isin}
                onClick={() => onSelect?.(s.isin)}
                disabled={!onSelect}
                className="w-full flex items-center justify-between px-3 py-2 text-xs text-left enabled:hover:bg-zinc-900/70 enabled:cursor-pointer transition-colors"
              >
                <span className="font-mono text-zinc-400">{s.nse_symbol ?? s.isin}</span>
                <span className="text-zinc-500">
                  {s.coupon_rate != null ? `${Number(s.coupon_rate).toFixed(2)}%` : ""} ·{" "}
                  {fmt.date(s.redemption_date)} ·{" "}
                  {s.close != null ? fmt.inr(Number(s.close)) : "untraded"}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {b.documents.filter((d) => d.download_link).length > 0 && (
        <div>
          <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">
            Documents ({b.documents.filter((d) => d.download_link).length})
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {b.documents
              .filter((d) => d.download_link)
              .map((d, i) => (
                <a
                  key={i}
                  href={d.download_link!}
                  target="_blank"
                  rel="noreferrer"
                  title={d.doc_name ?? undefined}
                  className="text-xs px-2.5 py-1.5 rounded-md border border-zinc-800 bg-zinc-900/50 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200 transition-colors max-w-56 truncate"
                >
                  {docLabel(d.doc_name)} ↗
                </a>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}
