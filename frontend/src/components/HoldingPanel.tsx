import type { ReactNode } from "react";
import { fmt } from "../api/client";
import { bondIncome, usePortfolio } from "../lib/portfolio";
import LotsPanel from "./LotsPanel";
import StatBox from "./StatBox";

/** Drawer tab for a bond the user holds: position stats, income from
 * this bond, and the editable lots table. Everything comes from the
 * cached /api/portfolio response — no extra endpoint. */
export default function HoldingPanel({ isin }: { isin: string }) {
  const { data, isLoading } = usePortfolio();
  const p = data?.positions.find((x) => x.isin === isin);

  if (isLoading || !data) {
    return <div className="text-sm text-zinc-500 animate-pulse">Loading holding…</div>;
  }
  if (!p) {
    return <div className="text-sm text-zinc-500">You no longer hold this bond.</div>;
  }

  const income = bondIncome(data.cashflows, isin);
  const portfolioPct =
    p.value != null && data.summary.value ? (p.value / data.summary.value) * 100 : null;
  const avgPrice = p.invested != null && p.units > 0 ? p.invested / p.units : null;
  const np = p.next_payment;

  return (
    <div className="space-y-6">
      <Section title="Position">
        <StatBox label="Units" value={fmt.num(p.units, 2)} />
        <StatBox label="Avg buy price" value={fmt.inrFull(avgPrice)} />
        <StatBox label="Invested" value={fmt.inrFull(p.invested)} />
        <StatBox label="Current value" value={fmt.inrFull(p.value)} accent />
        <GainBox gain={p.gain} gainPct={p.gain_pct} />
        <StatBox label="% of portfolio" value={fmt.pct(portfolioPct)} />
        {np && (
          <StatBox
            label="Next payment"
            value={`${fmt.inr(np.amount)} ${np.event} · ${fmt.date(np.due_date)}`}
            badge={np.estimated ? "est." : undefined}
          />
        )}
      </Section>

      <Section title="Income from this bond">
        <StatBox
          label="Received so far"
          value={fmt.inr(income.received)}
          tip="Payments in months already over, scaled to the units you held on each record date."
        />
        <StatBox
          label="Projected"
          value={fmt.inr(income.projected)}
          tip="Future payments at current holdings; the current month counts fully as projected."
        />
      </Section>

      <div>
        <SectionHeading title={`Lots (${p.lots.length})`} />
        <LotsPanel p={p} />
      </div>
    </div>
  );
}

/** Unrealized P&L card; StatBox can't render red, so this mirrors its
 * markup with the sign-dependent colour. */
function GainBox({ gain, gainPct }: { gain: number | null; gainPct: number | null }) {
  const color = gain == null ? "text-zinc-200" : gain >= 0 ? "text-emerald-400" : "text-red-400";
  const value =
    gain == null ? "–" : `${gain >= 0 ? "+" : ""}${fmt.inr(gain)} (${fmt.pct(gainPct)})`;
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 px-3 py-2 h-full">
      <div className="text-[10px] text-zinc-500">Unrealized P&L</div>
      <div className={`text-xs font-semibold mt-0.5 break-words line-clamp-2 ${color}`}>
        {value}
      </div>
    </div>
  );
}

function SectionHeading({ title }: { title: string }) {
  return (
    <h3 className="flex items-center gap-2 text-xs font-bold text-zinc-200 uppercase tracking-wider mb-2.5">
      <span className="h-3.5 w-1 rounded-full bg-emerald-500/80" />
      {title}
      <span className="flex-1 border-t border-zinc-800" />
    </h3>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <SectionHeading title={title} />
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">{children}</div>
    </div>
  );
}
