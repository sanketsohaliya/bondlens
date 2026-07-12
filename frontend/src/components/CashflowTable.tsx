import { fmt } from "../api/client";
import type { BondDetail } from "../api/types";

export default function CashflowTable({ bond: b }: { bond: BondDetail }) {
  const future = b.cashflows.filter((f) => f.future);
  const past = b.cashflows.filter((f) => !f.future);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 text-xs text-zinc-500">
        <span>
          Schedule: <span className="text-zinc-300 capitalize">{b.schedule_shape}</span>
        </span>
        <span>
          {future.length} future / {b.cashflows.length} total flows
        </span>
        {b.amounts_estimated && <span className="text-amber-500">some amounts estimated</span>}
      </div>

      <Section title={`Future (${fmt.inr(b.future_total_per_unit)}/unit)`} flows={future} highlight />
      {past.length > 0 && <Section title="Past" flows={past.slice().reverse()} />}
    </div>
  );
}

function Section({
  title,
  flows,
  highlight,
}: {
  title: string;
  flows: BondDetail["cashflows"];
  highlight?: boolean;
}) {
  if (flows.length === 0) return null;
  return (
    <div>
      <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">{title}</h3>
      <div className="rounded-lg border border-zinc-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-zinc-500 bg-zinc-900/60 border-b border-zinc-800">
              <th className="px-3 py-2 font-medium">Due</th>
              <th className="px-3 py-2 font-medium">Event</th>
              <th className="px-3 py-2 font-medium">Record</th>
              <th className="px-3 py-2 font-medium text-right">Amount / unit</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/60">
            {flows.map((f, i) => (
              <tr key={i} className={highlight ? "" : "opacity-60"}>
                <td className="px-3 py-2 whitespace-nowrap">{fmt.date(f.due_date)}</td>
                <td className="px-3 py-2">
                  <span
                    className={
                      "text-xs px-1.5 py-0.5 rounded capitalize " +
                      (f.event === "redemption"
                        ? "bg-emerald-500/10 text-emerald-400"
                        : "bg-zinc-500/10 text-zinc-400")
                    }
                  >
                    {f.event}
                  </span>
                </td>
                <td className="px-3 py-2 text-zinc-500 whitespace-nowrap">{fmt.date(f.record_date)}</td>
                <td className="px-3 py-2 text-right whitespace-nowrap">
                  {fmt.inr(f.amount)}
                  {f.estimated && (
                    <span className="text-amber-500 ml-1" title="estimated from coupon rate">
                      ~
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
