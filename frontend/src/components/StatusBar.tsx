import { useQuery } from "@tanstack/react-query";
import { api, fmt } from "../api/client";

// Replaces the old fetch buttons: data arrives on schedule, this shows how
// fresh it is.
export default function StatusBar() {
  const { data } = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
    refetchInterval: 5 * 60_000,
  });

  if (!data) return null;

  const items: [string, string][] = [
    ["Universe", data.universe_run ? fmt.date(data.universe_run.run_at) : "–"],
    ["NSDL", data.nsdl_run ? fmt.date(data.nsdl_run.run_at) : "–"],
    ["Trades", fmt.date(data.latest_trade_date)],
  ];

  return (
    <div className="hidden md:flex items-center gap-4 text-xs text-zinc-500">
      {items.map(([label, value]) => (
        <span key={label} className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
          {label}: <span className="text-zinc-300">{value}</span>
        </span>
      ))}
    </div>
  );
}
