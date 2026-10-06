import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, fmt } from "../api/client";
import type { AlertRule, OpportunityHit } from "../api/types";
import { describeConditions, describeRuleFilters } from "../lib/alertRules";
import { fmtIstTime } from "../lib/quoteValuation";

/** The bell's Alerts tab: push topic, saved rules, exit targets, the
 * recent hits with their tick-to-push latency, and the watcher's pulse. */
export default function AlertsPanel({
  onSelect,
}: {
  onSelect: (isin: string, tab?: "order book") => void;
}) {
  const qc = useQueryClient();
  const overview = useQuery({ queryKey: ["alerts"], queryFn: api.alerts.overview });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["alerts"] });
  const [topic, setTopic] = useState<string | null>(null);
  const saveTopic = useMutation({
    mutationFn: (t: string) => api.alerts.putSettings({ ntfy_topic: t || null }),
    onSuccess: () => {
      setTopic(null);
      invalidate();
    },
  });
  const test = useMutation({ mutationFn: api.alerts.test });
  const patch = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.alerts.updateRule(id, { enabled }),
    onSuccess: invalidate,
  });
  const remove = useMutation({ mutationFn: api.alerts.deleteRule, onSuccess: invalidate });
  const removeTarget = useMutation({ mutationFn: api.alerts.deleteTarget, onSuccess: invalidate });

  if (overview.isPending) return <div className="p-3 text-xs text-zinc-500">Loading alerts…</div>;
  if (overview.isError || !overview.data)
    return <div className="p-3 text-xs text-amber-400">Could not load alerts.</div>;
  const { rules, targets, settings, recent, watcher } = overview.data;
  const shownTopic = topic ?? settings.ntfy_topic ?? "";
  const input =
    "flex-1 rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-100 focus:border-emerald-500 focus:outline-none";

  return (
    <div className="max-h-[32rem] overflow-y-auto text-xs">
      <section className="px-3 py-2.5 border-b border-zinc-800">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
          Phone push (ntfy topic)
        </div>
        <div className="mt-1.5 flex items-center gap-2">
          <input
            className={input}
            placeholder="e.g. bondlens-opps-x7k2m9 (keep it private)"
            value={shownTopic}
            onChange={(e) => setTopic(e.target.value)}
          />
          <button
            type="button"
            disabled={topic === null || saveTopic.isPending}
            onClick={() => saveTopic.mutate(shownTopic.trim())}
            className="px-2 py-1 rounded-md border border-zinc-700 text-zinc-300 hover:border-zinc-500 disabled:opacity-40"
          >
            Save
          </button>
          <button
            type="button"
            disabled={!settings.ntfy_topic || test.isPending}
            onClick={() => test.mutate()}
            title="Send one test push to this topic"
            className="px-2 py-1 rounded-md border border-zinc-700 text-zinc-300 hover:border-zinc-500 disabled:opacity-40"
          >
            {test.isPending ? "Sending…" : test.isSuccess ? "Sent ✓" : "Test"}
          </button>
        </div>
        {(saveTopic.isError || test.isError) && (
          <p className="mt-1 text-red-400">{((saveTopic.error || test.error) as Error).message}</p>
        )}
        <p className="mt-1.5 text-[11px] text-zinc-600">
          Subscribe to the topic in the ntfy app and switch it to <b>Instant delivery</b> — that is
          where most push latency hides. Alerts arrive as urgent (they break through Do Not
          Disturb).
        </p>
      </section>

      <section className="px-3 py-2.5 border-b border-zinc-800">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
          Rules ({rules.length})
        </div>
        {rules.length === 0 && (
          <p className="mt-1.5 text-zinc-500">
            None yet. Set filters on Explore and press <b>Alert</b>.
          </p>
        )}
        <ul className="mt-1.5 divide-y divide-zinc-800/60">
          {rules.map((r) => (
            <RuleRow
              key={r.id}
              rule={r}
              onToggle={(enabled) => patch.mutate({ id: r.id, enabled })}
              onDelete={() => remove.mutate(r.id)}
            />
          ))}
        </ul>
      </section>

      {targets.length > 0 && (
        <section className="px-3 py-2.5 border-b border-zinc-800">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
            Exit targets ({targets.length})
          </div>
          <ul className="mt-1.5 divide-y divide-zinc-800/60">
            {targets.map((t) => (
              <li key={t.isin} className="flex items-center gap-2 py-1.5">
                <button
                  type="button"
                  onClick={() => onSelect(t.isin, "order book")}
                  className="flex-1 text-left text-zinc-200 hover:text-emerald-400"
                >
                  {t.isin}
                  <span className="ml-2 text-zinc-500">
                    {[
                      t.min_bid_price != null ? `bid ≥ ${fmt.inrFull(t.min_bid_price)}` : null,
                      t.max_bid_ytm != null ? `bid YTM ≤ ${t.max_bid_ytm}%` : null,
                    ]
                      .filter(Boolean)
                      .join(" or ")}
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => removeTarget.mutate(t.isin)}
                  className="text-zinc-600 hover:text-red-400"
                  title="Remove target"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="px-3 py-2.5 border-b border-zinc-800">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
          Recent hits
        </div>
        {recent.length === 0 && <p className="mt-1.5 text-zinc-500">Nothing fired yet.</p>}
        <ul className="mt-1.5 divide-y divide-zinc-800/60">
          {recent.slice(0, 20).map((h) => (
            <HitRow key={h.id} hit={h} onOpen={() => onSelect(h.isin, "order book")} />
          ))}
        </ul>
      </section>

      <section className="px-3 py-2 text-[11px] text-zinc-600">
        {watcher ? (
          <>
            Watcher: {String(watcher.note ?? "")} · {String(watcher.users)} user(s) ·{" "}
            {Number(watcher.tokens).toLocaleString("en-IN")} instruments · pushed p50{" "}
            {watcher.push_p50_ms != null ? `${watcher.push_p50_ms} ms` : "–"} / p99{" "}
            {watcher.push_p99_ms != null ? `${watcher.push_p99_ms} ms` : "–"} · beat{" "}
            {fmtIstTime(String(watcher.beat_at), true) ?? "–"} IST
            {watcher.thresholds_ready ? "" : " · thresholds still building"}
          </>
        ) : (
          "Watcher has not reported yet."
        )}
      </section>
    </div>
  );
}

function RuleRow({
  rule,
  onToggle,
  onDelete,
}: {
  rule: AlertRule;
  onToggle: (enabled: boolean) => void;
  onDelete: () => void;
}) {
  return (
    <li className="flex items-start gap-2 py-1.5">
      <input
        type="checkbox"
        checked={rule.enabled}
        onChange={(e) => onToggle(e.target.checked)}
        title={rule.enabled ? "Armed" : "Paused"}
        className="mt-0.5 accent-emerald-500"
      />
      <div className="flex-1 min-w-0">
        <div className={`truncate ${rule.enabled ? "text-zinc-200" : "text-zinc-500"}`}>
          {rule.name}
        </div>
        <div className="text-[11px] text-zinc-500 truncate">
          {describeConditions(rule)} · {describeRuleFilters(rule.filters ?? {})}
        </div>
        {rule.last_hit_at && (
          <div className="text-[10px] text-zinc-600">
            last hit{" "}
            {new Date(rule.last_hit_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={onDelete}
        className="text-zinc-600 hover:text-red-400"
        title="Delete rule"
      >
        ×
      </button>
    </li>
  );
}

function HitRow({ hit, onOpen }: { hit: OpportunityHit; onOpen: () => void }) {
  const name = hit.issuer_name ?? hit.nse_symbol ?? hit.bse_scrip_id ?? hit.isin;
  const lead =
    hit.side === "ask"
      ? `${hit.ytm != null ? `${fmt.pct(hit.ytm)} @ ` : ""}${fmt.inrFull(hit.price)} × ${fmt.num(hit.qty, 0)}`
      : `bid ${fmt.inrFull(hit.price)} × ${fmt.num(hit.qty, 0)}`;
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="w-full py-1.5 text-left hover:bg-zinc-800/60"
      >
        <div className="flex items-center gap-2">
          <span className={hit.side === "ask" ? "text-emerald-400" : "text-sky-400"}>
            {hit.side === "ask" ? "▲" : "▼"}
          </span>
          <span className="flex-1 truncate text-zinc-200">
            {lead} — {name}
          </span>
          <span className="text-[10px] text-zinc-600 shrink-0">
            {fmtIstTime(hit.fired_at) ?? ""}
            {hit.push_ms != null ? ` · ${hit.push_ms} ms` : ""}
          </span>
        </div>
      </button>
    </li>
  );
}
