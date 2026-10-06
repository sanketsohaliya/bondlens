import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, fmt } from "../api/client";
import type { AlertRule, ExploreParams } from "../api/types";
import {
  describeConditions,
  describeRuleFilters,
  groupHits,
  type HitGroup,
  watcherStatus,
} from "../lib/alertRules";
import { fmtIstTime } from "../lib/quoteValuation";
import SaveAlertDialog from "./SaveAlertDialog";

const TAB = "text-xs font-medium px-3 py-2 border-b-2 transition-colors -mb-px " as const;

/** The Alert chip's dropdown: status header, gear-hidden push topic, and
 * two tabs - the rules (arm/pause, edit, delete) and today's hits grouped
 * per bond. Hits open the bond's Order Book. */
export default function AlertsPanel({
  params,
  onSelect,
}: {
  params: ExploreParams;
  onSelect: (isin: string, tab?: "order book") => void;
}) {
  const qc = useQueryClient();
  const overview = useQuery({ queryKey: ["alerts"], queryFn: api.alerts.overview });
  const { data: kite } = useQuery({
    queryKey: ["kite-status"],
    queryFn: api.kite.status,
    staleTime: 120_000,
  });
  const [tab, setTab] = useState<"rules" | "hits">("rules");
  const [settings, setSettings] = useState(false);
  const [dialog, setDialog] = useState<{ params?: ExploreParams; rule?: AlertRule } | null>(null);

  if (overview.isPending) return <div className="p-4 text-xs text-zinc-500">Loading alerts…</div>;
  if (overview.isError || !overview.data)
    return <div className="p-4 text-xs text-amber-400">Could not load alerts.</div>;
  const { rules, recent, hits_today, watcher } = overview.data;
  const groups = groupHits(recent);
  const today = recent[0]?.trade_day;
  const todayCount = recent.filter((h) => h.trade_day === today).length;
  const status = watcherStatus(watcher, rules.length, kite?.connected ?? false);
  const dot =
    status.tone === "live"
      ? "bg-emerald-400"
      : status.tone === "warn"
        ? "bg-amber-400"
        : "bg-zinc-600";

  return (
    <div className="text-xs">
      <div className="flex items-center gap-3 px-4 pt-3 pb-2">
        <span className="font-bold uppercase tracking-wider text-zinc-200">Alerts</span>
        <span className="flex items-center gap-1.5 text-[11px] text-zinc-500">
          <span className={`inline-block w-1.5 h-1.5 rounded-full ${dot}`} />
          {status.label}
        </span>
        <span className="flex-1" />
        <button
          type="button"
          title="Push settings (ntfy topic)"
          onClick={() => setSettings(!settings)}
          className={`p-1 rounded hover:text-zinc-200 ${settings ? "text-zinc-200" : "text-zinc-500"}`}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
          </svg>
        </button>
      </div>

      {settings && <TopicRow topic={overview.data.settings.ntfy_topic ?? null} />}

      <div className="flex items-center border-b border-zinc-800 px-2">
        <button
          type="button"
          onClick={() => setTab("rules")}
          className={`${TAB}${tab === "rules" ? "border-emerald-500 text-emerald-400" : "border-transparent text-zinc-500 hover:text-zinc-300"}`}
        >
          Rules ({rules.length})
        </button>
        <button
          type="button"
          onClick={() => setTab("hits")}
          className={`${TAB}${tab === "hits" ? "border-emerald-500 text-emerald-400" : "border-transparent text-zinc-500 hover:text-zinc-300"}`}
        >
          Hits {todayCount > 0 && `(${todayCount} today)`}
        </button>
      </div>

      <div className="max-h-[26rem] overflow-y-auto">
        {tab === "rules" && (
          <>
            {rules.length === 0 && (
              <p className="px-4 py-6 text-center text-zinc-500">
                No rules yet — set filters on Explore and save them as a rule.
              </p>
            )}
            <ul className="divide-y divide-zinc-800/60">
              {rules.map((r) => (
                <RuleRow
                  key={r.id}
                  rule={r}
                  hitsToday={hits_today?.[`rule:${r.id}`] ?? 0}
                  onEdit={() => setDialog({ rule: r })}
                  onChanged={() => qc.invalidateQueries({ queryKey: ["alerts"] })}
                />
              ))}
            </ul>
            <button
              type="button"
              onClick={() => setDialog({ params })}
              className="w-full px-4 py-2.5 text-left text-zinc-400 hover:text-emerald-400 hover:bg-zinc-800/40 border-t border-zinc-800/60"
            >
              + Save current filters as a rule
            </button>
          </>
        )}
        {tab === "hits" && (
          <>
            {groups.length === 0 && (
              <p className="px-4 py-6 text-center text-zinc-500">Nothing has fired yet.</p>
            )}
            <ul className="divide-y divide-zinc-800/60">
              {groups.map((g) => (
                <HitRow key={g.key} group={g} onOpen={() => onSelect(g.isin, "order book")} />
              ))}
            </ul>
          </>
        )}
      </div>

      {dialog && (
        <SaveAlertDialog
          params={dialog.params}
          rule={dialog.rule}
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  );
}

function TopicRow({ topic }: { topic: string | null }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (t: string) => api.alerts.putSettings({ ntfy_topic: t || null }),
    onSuccess: () => {
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
  const test = useMutation({ mutationFn: api.alerts.test });
  const value = draft ?? topic ?? "";
  return (
    <div className="px-4 pb-3">
      <div className="flex items-center gap-2">
        <input
          className="flex-1 rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-100 focus:border-emerald-500 focus:outline-none"
          placeholder="ntfy topic (keep it private)"
          value={value}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button
          type="button"
          disabled={draft === null || save.isPending}
          onClick={() => save.mutate(value.trim())}
          className="px-2 py-1 rounded-md border border-zinc-700 text-zinc-300 hover:border-zinc-500 disabled:opacity-40"
        >
          Save
        </button>
        <button
          type="button"
          disabled={!topic || test.isPending}
          onClick={() => test.mutate()}
          title="Send one test push. In the ntfy app, switch this topic to Instant delivery for the fastest path; alerts arrive as urgent."
          className="px-2 py-1 rounded-md border border-zinc-700 text-zinc-300 hover:border-zinc-500 disabled:opacity-40"
        >
          {test.isPending ? "Sending…" : test.isSuccess ? "Sent ✓" : "Test"}
        </button>
      </div>
      {(save.isError || test.isError) && (
        <p className="mt-1 text-red-400">{((save.error || test.error) as Error).message}</p>
      )}
    </div>
  );
}

function Switch({
  on,
  onChange,
  title,
}: {
  on: boolean;
  onChange: (next: boolean) => void;
  title: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      title={title}
      onClick={() => onChange(!on)}
      className={`relative inline-flex h-4 w-7 shrink-0 items-center rounded-full transition-colors ${
        on ? "bg-emerald-500" : "bg-zinc-700"
      }`}
    >
      <span
        className={`inline-block h-3 w-3 rounded-full bg-zinc-950 shadow transition-transform ${
          on ? "translate-x-3.5" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}

function RuleRow({
  rule,
  hitsToday,
  onEdit,
  onChanged,
}: {
  rule: AlertRule;
  hitsToday: number;
  onEdit: () => void;
  onChanged: () => void;
}) {
  const [confirm, setConfirm] = useState(false);
  const enabled = rule.enabled ?? true;
  const patch = useMutation({
    mutationFn: (enabled: boolean) => api.alerts.updateRule(rule.id, { enabled }),
    onSuccess: onChanged,
  });
  const remove = useMutation({
    mutationFn: () => api.alerts.deleteRule(rule.id),
    onSuccess: onChanged,
  });
  const meta = [
    describeRuleFilters(rule.filters ?? {}),
    hitsToday ? `${hitsToday} hit${hitsToday === 1 ? "" : "s"} today` : null,
    rule.last_hit_at ? `last ${fmtIstTime(rule.last_hit_at) ?? ""}` : null,
  ].filter(Boolean);
  return (
    <li className={`px-4 py-2.5 ${rule.enabled ? "" : "opacity-60"}`}>
      <div className="flex items-center gap-3">
        <div className="flex-1 min-w-0">
          <div className="truncate text-zinc-200" title={describeConditions(rule)}>
            {rule.name}
          </div>
          <div className="mt-0.5 truncate text-[11px] text-zinc-500">{meta.join(" · ")}</div>
        </div>
        <Switch
          on={enabled}
          onChange={(v) => patch.mutate(v)}
          title={enabled ? "Armed — click to pause" : "Paused — click to arm"}
        />
        <button
          type="button"
          onClick={onEdit}
          title="Edit thresholds or name"
          className="text-zinc-500 hover:text-zinc-200"
        >
          ✎
        </button>
        {confirm ? (
          <span className="flex items-center gap-1 text-[11px]">
            <button
              type="button"
              onClick={() => remove.mutate()}
              className="text-red-400 hover:text-red-300"
            >
              delete
            </button>
            <span className="text-zinc-600">/</span>
            <button
              type="button"
              onClick={() => setConfirm(false)}
              className="text-zinc-400 hover:text-zinc-200"
            >
              keep
            </button>
          </span>
        ) : (
          <button
            type="button"
            onClick={() => setConfirm(true)}
            title="Delete rule"
            className="text-zinc-600 hover:text-red-400"
          >
            ×
          </button>
        )}
      </div>
    </li>
  );
}

function HitRow({ group: g, onOpen }: { group: HitGroup; onOpen: () => void }) {
  const h = g.latest;
  const pill = g.short
    ? "bg-amber-500/15 text-amber-400 border-amber-500/30"
    : g.side === "ask"
      ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/30"
      : "bg-sky-500/15 text-sky-400 border-sky-500/30";
  const qty = h.qty ?? 0;
  const ticket = qty > 0 ? ` = ${fmt.inrFull(h.price * qty)}` : "";
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        title={h.push_ms != null ? `tick → push ${h.push_ms} ms` : undefined}
        className="w-full px-4 py-2.5 text-left hover:bg-zinc-800/50"
      >
        <div className="flex items-center gap-2">
          <span className="flex-1 truncate text-zinc-200">{g.name}</span>
          <span
            className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded border ${pill}`}
            title={g.short ? "Exact yield came in under the rule" : undefined}
          >
            {g.side === "bid" ? "bid " : ""}
            {h.ytm != null ? fmt.pct(h.ytm) : "–"}
          </span>
          <span className="shrink-0 text-[10px] text-zinc-600">{fmtIstTime(h.fired_at) ?? ""}</span>
        </div>
        <div className="mt-0.5 flex items-center gap-2 text-[11px] text-zinc-500">
          <span className="flex-1 truncate">
            {fmt.inrFull(h.price)} × {fmt.num(qty, 0)}
            {ticket}
            {h.exchange ? ` · ${h.exchange}` : ""}
          </span>
          {g.count > 1 && (
            <span className="shrink-0">
              {g.count} {g.side === "ask" ? "asks" : "bids"} today
              {g.bestYtm != null ? ` · best ${fmt.pct(g.bestYtm)}` : ""}
            </span>
          )}
        </div>
      </button>
    </li>
  );
}
