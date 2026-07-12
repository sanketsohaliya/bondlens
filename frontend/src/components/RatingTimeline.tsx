import { fmt } from "../api/client";
import type { RatingEvent } from "../api/types";

export default function RatingTimeline({ timeline }: { timeline: Record<string, RatingEvent[]> }) {
  const agencies = Object.keys(timeline);
  if (agencies.length === 0) {
    return <div className="text-sm text-zinc-500 p-4">No rating history.</div>;
  }
  return (
    <div className="space-y-5">
      {agencies.map((agency) => (
        <div key={agency}>
          <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-2">
            {agency}
          </h3>
          <ol className="relative border-l border-zinc-800 ml-1.5 space-y-3">
            {timeline[agency].map((e, i) => (
              <li key={i} className="ml-4">
                <span
                  className={
                    "absolute -left-[5px] mt-1.5 w-2.5 h-2.5 rounded-full border-2 border-zinc-950 " +
                    (i === 0 ? "bg-emerald-500" : "bg-zinc-600")
                  }
                />
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                  <span className={`font-semibold ${i === 0 ? "text-zinc-100" : "text-zinc-400"}`}>
                    {e.rating ?? "?"}
                    {e.outlook && e.outlook.toLowerCase() !== "no outlook" && (
                      <span className="text-zinc-500 font-normal">/{e.outlook}</span>
                    )}
                  </span>
                  {e.action && <span className="text-xs text-zinc-500">{e.action}</span>}
                  <span className="text-xs text-zinc-600">{fmt.date(e.rating_date)}</span>
                  {e.press_release && (
                    <a
                      href={e.press_release}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs text-sky-400 hover:underline"
                    >
                      press release ↗
                    </a>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>
      ))}
    </div>
  );
}
