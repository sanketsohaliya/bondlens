import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, fmt } from "../api/client";
import type { AlertRule, AutoOrder, ExploreParams } from "../api/types";
import { groupHitsByRule, type HitGroup, watcherStatus } from "../lib/alertRules";
import { fmtIstTime } from "../lib/quoteValuation";
import SaveAlertDialog from "./SaveAlertDialog";

const PILL =
  "text-[10px] px-1.5 py-0.5 rounded border border-zinc-700/70 text-zinc-400 whitespace-nowrap";
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
  const overview = useQuery({ queryKey: ["alerts"], queryFn: () => api.alerts.overview() });
  const { data: kite } = useQuery({
    queryKey: ["kite-status"],
    queryFn: api.kite.status,
    staleTime: 120_000,
  });
  const [tab, setTab] = useState<"rules" | "hits">("rules");
  const [scope, setScope] = useState<"today" | "all">("today");
  const earlier = useQuery({
    queryKey: ["alerts", "hits", "all"],
    queryFn: () => api.alerts.overview("all"),
    enabled: scope === "all",
  });
  const [showSettings, setShowSettings] = useState(false);
  const [dialog, setDialog] = useState<{ params?: ExploreParams; rule?: AlertRule } | null>(null);

  if (overview.isPending) return <div className="p-4 text-xs text-zinc-500">Loading alerts…</div>;
  if (overview.isError || !overview.data)
    return <div className="p-4 text-xs text-amber-400">Could not load alerts.</div>;
  const { rules, recent, hits_today, watcher, auto_orders, spent_today, settings } = overview.data;
  const armedCount = rules.filter((r) => r.auto_buy).length;
  const ordersByAlert = new Map<number, AutoOrder>();
  for (const o of auto_orders ?? []) if (o.alert_id != null) ordersByAlert.set(o.alert_id, o);
  const shownHits = scope === "all" && earlier.data ? earlier.data.recent : recent;
  const buckets = groupHitsByRule(shownHits);
  const todayCount = recent.length;
  const todayKey = recent[0]?.trade_day;
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
          {status.detail && <span className={PILL}>{status.detail}</span>}
          {armedCount > 0 && (
            <span
              className="text-[10px] px-1.5 py-0.5 rounded border border-amber-500/40 text-amber-400 whitespace-nowrap"
              title="Rules that will place buy orders on a hit until 15:45"
            >
              auto-buy armed ({armedCount})
            </span>
          )}
        </span>
        <span className="flex-1" />
        <button
          type="button"
          title="Push settings (ntfy topic)"
          onClick={() => setShowSettings(!showSettings)}
          className={`p-1 rounded hover:text-zinc-200 ${showSettings ? "text-zinc-200" : "text-zinc-500"}`}
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

      {showSettings && (
        <SettingsRows
          topic={settings.ntfy_topic ?? null}
          cap={settings.auto_buy_daily_cap ?? null}
          armedCount={armedCount}
        />
      )}

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
                  spentToday={spent_today?.[`rule:${r.id}`] ?? 0}
                  kiteConnected={kite?.connected ?? false}
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
            {buckets.length === 0 && (
              <p className="px-4 py-6 text-center text-zinc-500">
                {scope === "today" ? "Nothing has fired today." : "Nothing has fired yet."}
              </p>
            )}
            {buckets.map((b) => (
              <section key={b.key} className="border-b border-zinc-800/60 last:border-b-0">
                <div className="flex items-center gap-2 px-4 pt-2.5 pb-1 text-[11px] text-zinc-400">
                  <span className="min-w-0 flex-1 leading-snug">{b.label}</span>
                  {scope === "all" && b.day !== todayKey && (
                    <span className="shrink-0 text-zinc-600">{fmtDay(b.day)}</span>
                  )}
                  <span className={PILL}>{b.groups.length}</span>
                </div>
                <ul>
                  {b.groups.map((g) => (
                    <HitRow
                      key={g.key}
                      group={g}
                      order={ordersByAlert.get(g.latest.id)}
                      onOpen={() => onSelect(g.isin, "order book")}
                    />
                  ))}
                </ul>
              </section>
            ))}
            <button
              type="button"
              onClick={() => setScope(scope === "today" ? "all" : "today")}
              className="w-full px-4 py-2.5 text-left text-zinc-500 hover:text-zinc-300 border-t border-zinc-800/60"
            >
              {scope === "today"
                ? "Show earlier"
                : earlier.isPending
                  ? "Loading…"
                  : "Show today only"}
            </button>
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

function SettingsRows({
  topic,
  cap,
  armedCount,
}: {
  topic: string | null;
  cap: number | null;
  armedCount: number;
}) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["alerts"] });
  const [draft, setDraft] = useState<string | null>(null);
  const [capDraft, setCapDraft] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (t: string) => api.alerts.putSettings({ ntfy_topic: t || null }),
    onSuccess: () => {
      setDraft(null);
      invalidate();
    },
  });
  const saveCap = useMutation({
    mutationFn: (c: string) => api.alerts.putSettings({ auto_buy_daily_cap: c ? Number(c) : null }),
    onSuccess: () => {
      setCapDraft(null);
      invalidate();
    },
  });
  const disarm = useMutation({ mutationFn: api.alerts.disarmAll, onSuccess: invalidate });
  const test = useMutation({ mutationFn: api.alerts.test });
  const value = draft ?? topic ?? "";
  const capValue = capDraft ?? (cap != null ? String(cap) : "");
  return (
    <div className="px-4 pb-3 space-y-2">
      <div className="flex items-center gap-2">
        <span className="w-24 shrink-0 text-[11px] text-zinc-500">Push topic</span>
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
      <div className="flex items-center gap-2">
        <span className="w-24 shrink-0 text-[11px] text-zinc-500">Auto-buy cap</span>
        <input
          className="flex-1 rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-100 focus:border-emerald-500 focus:outline-none"
          inputMode="decimal"
          placeholder="₹ across all rules per day (optional)"
          value={capValue}
          onChange={(e) => setCapDraft(e.target.value)}
        />
        <button
          type="button"
          disabled={capDraft === null || saveCap.isPending}
          onClick={() => saveCap.mutate(capValue.trim())}
          className="px-2 py-1 rounded-md border border-zinc-700 text-zinc-300 hover:border-zinc-500 disabled:opacity-40"
        >
          Save
        </button>
        <button
          type="button"
          disabled={armedCount === 0 || disarm.isPending}
          onClick={() => disarm.mutate()}
          title="Kill switch: disarm auto-buy on every rule"
          className="px-2 py-1 rounded-md border border-red-500/50 text-red-400 hover:bg-red-500/10 disabled:opacity-40"
        >
          Disarm all
        </button>
      </div>
      {(save.isError || test.isError || saveCap.isError) && (
        <p className="mt-1 text-red-400">
          {((save.error || test.error || saveCap.error) as Error).message}
        </p>
      )}
    </div>
  );
}

export function Switch({
  on,
  onChange,
  title,
  tone = "emerald",
  disabled = false,
}: {
  on: boolean;
  onChange: (next: boolean) => void;
  title: string;
  tone?: "emerald" | "amber";
  disabled?: boolean;
}) {
  const onClass = tone === "amber" ? "bg-amber-500" : "bg-emerald-500";
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      title={title}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative inline-flex h-4 w-7 shrink-0 items-center rounded-full transition-colors disabled:opacity-40 ${
        on ? onClass : "bg-zinc-700"
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
  spentToday,
  kiteConnected,
  onEdit,
  onChanged,
}: {
  rule: AlertRule;
  hitsToday: number;
  spentToday: number;
  kiteConnected: boolean;
  onEdit: () => void;
  onChanged: () => void;
}) {
  const [confirm, setConfirm] = useState(false);
  const enabled = rule.enabled ?? true;
  const canArm = rule.buy_budget != null && rule.buy_daily_cap != null;
  const arm = useMutation({
    mutationFn: (on: boolean) => api.alerts.armAutoBuy(rule.id, on),
    onSuccess: onChanged,
  });
  const armTitle = !canArm
    ? "Set a budget per hit and a daily cap (✎) to enable auto-buy"
    : !kiteConnected && !rule.auto_buy
      ? "Connect Kite to arm auto-buy"
      : rule.auto_buy
        ? `Auto-buy armed until 15:45 — buys up to ₹${rule.buy_budget?.toLocaleString("en-IN")} per hit, click to disarm`
        : "Arm auto-buy for today";
  const patch = useMutation({
    mutationFn: (enabled: boolean) => api.alerts.updateRule(rule.id, { enabled }),
    onSuccess: onChanged,
  });
  const remove = useMutation({
    mutationFn: () => api.alerts.deleteRule(rule.id),
    onSuccess: onChanged,
  });
  const stats = [
    hitsToday ? `${hitsToday} hit${hitsToday === 1 ? "" : "s"} today` : null,
    rule.last_hit_at ? `last ${fmtIstTime(rule.last_hit_at) ?? ""}` : null,
    rule.buy_daily_cap != null && (spentToday > 0 || rule.auto_buy)
      ? `bought ${fmt.inrFull(spentToday)} of ${fmt.inr(rule.buy_daily_cap)} today`
      : null,
  ].filter(Boolean);
  return (
    <li className={`px-4 py-2.5 ${enabled ? "" : "opacity-60"}`}>
      <div className="flex items-center gap-3">
        <div className="flex-1 min-w-0">
          <div className="text-zinc-200 leading-snug">{rule.label}</div>
          {stats.length > 0 && (
            <div className="mt-0.5 flex gap-3 text-[11px] text-zinc-500">
              {stats.map((x) => (
                <span key={x}>{x}</span>
              ))}
            </div>
          )}
        </div>
        <Switch
          on={enabled}
          onChange={(v) => patch.mutate(v)}
          title={enabled ? "Alerting — click to pause" : "Paused — click to resume"}
        />
        <span className="flex items-center gap-1" title={armTitle}>
          <span className={`text-[10px] ${rule.auto_buy ? "text-amber-400" : "text-zinc-600"}`}>
            buy
          </span>
          <Switch
            on={Boolean(rule.auto_buy)}
            onChange={(v) => arm.mutate(v)}
            title={armTitle}
            tone="amber"
            disabled={!canArm || (!kiteConnected && !rule.auto_buy) || arm.isPending}
          />
        </span>
        <button
          type="button"
          onClick={onEdit}
          title="Edit thresholds"
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

function fmtDay(day: string): string {
  return new Date(`${day}T00:00:00+05:30`).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    timeZone: "Asia/Kolkata",
  });
}

function HitRow({
  group: g,
  order,
  onOpen,
}: {
  group: HitGroup;
  order?: AutoOrder;
  onOpen: () => void;
}) {
  const h = g.latest;
  const orderPill =
    order?.status === "placed"
      ? {
          text: `bought ${order.qty}`,
          cls: "border-emerald-500/40 text-emerald-400",
          tip: `Kite order ${order.kite_order_id ?? ""}, ${fmt.inrFull(order.value)}`,
        }
      : order?.status === "rejected"
        ? { text: "rejected", cls: "border-red-500/40 text-red-400", tip: order.reason ?? "" }
        : order?.status === "skipped"
          ? { text: "skipped", cls: "border-amber-500/40 text-amber-400", tip: order.reason ?? "" }
          : null;
  const pill = g.short
    ? "bg-amber-500/15 text-amber-400 border-amber-500/30"
    : g.side === "ask"
      ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/30"
      : "bg-sky-500/15 text-sky-400 border-sky-500/30";
  const qty = h.qty ?? 0;
  const details = [
    qty > 0 ? `ticket ${fmt.inrFull(h.price * qty)}` : null,
    h.exchange,
    g.count > 1 && g.bestYtm != null ? `best ${fmt.pct(g.bestYtm)}` : null,
    h.push_ms != null ? `tick → push ${h.push_ms} ms` : null,
    g.short ? "exact yield came in under the rule" : null,
  ]
    .filter(Boolean)
    .join(", ");
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        title={details}
        className="flex w-full items-center gap-2 px-4 py-1.5 text-left hover:bg-zinc-800/50"
      >
        <span className="min-w-0 flex-1 truncate text-zinc-200">{g.name}</span>
        {orderPill && (
          <span
            className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded border ${orderPill.cls}`}
            title={orderPill.tip}
          >
            {orderPill.text}
          </span>
        )}
        {g.count > 1 && <span className={PILL}>×{g.count}</span>}
        <span className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded border ${pill}`}>
          {g.side === "bid" ? "bid " : ""}
          {h.ytm != null ? fmt.pct(h.ytm) : "–"}
        </span>
        <span className="w-28 shrink-0 text-right text-[11px] text-zinc-400 tabular-nums">
          {fmt.inrFull(h.price)} × {fmt.num(qty, 0)}
        </span>
        <span className="w-10 shrink-0 text-right text-[10px] text-zinc-600 tabular-nums">
          {fmtIstTime(h.fired_at) ?? ""}
        </span>
      </button>
    </li>
  );
}
