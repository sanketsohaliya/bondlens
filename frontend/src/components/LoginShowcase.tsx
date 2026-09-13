import { useEffect, useRef, useState } from "react";
import InlineMetadata from "./InlineMetadata";

/* Marketing hero for the sign-in page: a perspective-tilted explorer
   panel with floating order-book / income / rating cards overlapping
   its corners, ticking continuously like a live terminal. Everything
   is FICTIONAL demo data — invented issuers, symbols and numbers —
   rendered in code so it stays crisp, weightless and on-theme. */

const TICK_MS = 2000;

const CHIPS = ["2,000+ listed NCDs", "Live YTM & ratings", "Broker-connected orders"];

// -- Explorer rows: [symbol, issuer, ytm, base price, rating, maturity, seed]
const ROWS = [
  ["9NBFC27", "NBFC FINANCE LTD", "11.84", 101250, "AA-", "Jun 2027", 1],
  ["11HFC29", "HOMEFIRST HOUSING", "12.40", 98730, "A+", "Mar 2029", 2],
  ["10MFI28", "GRAMEEN MICROFIN", "13.15", 99910, "A", "Sep 2028", 3],
  ["9IFRA31", "INFRA INVIT TRUST", "10.62", 104080, "AAA", "Dec 2031", 4],
  ["12GOLD26", "AURUM GOLD LOANS", "12.95", 100340, "A-", "Nov 2026", 5],
  ["10SFB28", "SARVODAYA SFB", "11.20", 99450, "AA-", "Feb 2028", 6],
  ["9PWR30", "JYOTI POWERGRID", "10.95", 102670, "AA+", "Aug 2030", 7],
  ["13MFL27", "MAITRI FINLEASE", "13.60", 97880, "BBB+", "Jan 2027", 8],
] as const;

const BIDS: [number, number][] = [
  [99750, 51],
  [99400, 120],
  [99100, 30],
];
const ASKS: [number, number][] = [
  [100150, 40],
  [100400, 90],
  [100900, 25],
];

const INCOME_BARS = [0.35, 0.35, 0.38, 0.6, 0.35, 0.38, 0.35, 1.0];

/** Deterministic wave in [-1, 1] — stable per (tick, seed), no Math.random
 * in render so React re-renders stay pure. */
const wave = (tick: number, seed: number) => Math.sin(tick * 3.7 + seed * 13.1);

const inr = (v: number) => `₹${v.toLocaleString("en-IN")}`;

const rise = (i: number): React.CSSProperties => ({
  animation: "rise-in .5s ease-out both",
  animationDelay: `${i * 70}ms`,
});

export default function LoginShowcase() {
  const [tick, setTick] = useState(0);
  const [reduced] = useState(
    () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
  );

  useEffect(() => {
    if (reduced) return;
    const t = setInterval(() => {
      if (!document.hidden) setTick((n) => n + 1);
    }, TICK_MS);
    return () => clearInterval(t);
  }, [reduced]);

  // Cursor parallax: the panel leans up to ±3° toward the pointer
  // (rAF-throttled, mouse only); cards counter-shift a few px for depth.
  const [tilt, setTilt] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const rafPending = useRef(false);
  const lastEvent = useRef<{ x: number; y: number }>({ x: 0, y: 0 });
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (reduced || e.pointerType !== "mouse") return;
    const box = e.currentTarget.getBoundingClientRect();
    lastEvent.current = {
      x: ((e.clientX - box.left) / box.width) * 2 - 1,
      y: ((e.clientY - box.top) / box.height) * 2 - 1,
    };
    if (!rafPending.current) {
      rafPending.current = true;
      requestAnimationFrame(() => {
        rafPending.current = false;
        setTilt(lastEvent.current);
      });
    }
  };
  const onPointerLeave = () => setTilt({ x: 0, y: 0 });

  return (
    <div className="select-none h-full flex flex-col justify-center" aria-hidden="true">
      {/* The hero's own header: tagline + proof pills, centered. */}
      <h2 className="text-center text-3xl xl:text-4xl [@media(max-height:820px)]:text-2xl font-extrabold tracking-tight text-zinc-100">
        Every listed corporate bond.{" "}
        <span className="bg-gradient-to-r from-emerald-300 to-emerald-500 bg-clip-text text-transparent">
          One lens.
        </span>
      </h2>
      <div className="mt-4 flex justify-center gap-2">
        {CHIPS.map((c) => (
          <span
            key={c}
            className="inline-flex items-center gap-1.5 rounded-full border border-zinc-800 bg-zinc-900/60 px-3 py-1 text-xs text-zinc-300"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            {c}
          </span>
        ))}
      </div>

      {/* Collage: tilted main panel + floating cards with symmetric overlaps.
          The panel eases toward the cursor; cards counter-shift for depth. */}
      <div
        className="relative mt-6 [@media(min-height:850px)]:mt-9 h-[clamp(17rem,44vh,26rem)] w-full max-w-5xl mx-auto"
        onPointerMove={onPointerMove}
        onPointerLeave={onPointerLeave}
      >
        <div
          className="absolute inset-x-10 inset-y-2 rounded-2xl border border-zinc-800 bg-zinc-900/70 backdrop-blur-sm ring-1 ring-emerald-500/10 shadow-[0_20px_80px_-20px_rgba(16,185,129,0.35)] overflow-hidden transition-transform duration-300 ease-out"
          style={{
            transform: `perspective(1600px) rotateY(${-4 + tilt.x * 3}deg) rotateX(${2 - tilt.y * 3}deg)`,
          }}
        >
          <div className="flex items-center gap-1.5 px-4 py-2.5 border-b border-zinc-800/80 bg-zinc-900/80">
            <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
            <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
            <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
            <span className="ml-3 text-[11px] text-zinc-500 font-medium">
              Bond<span className="text-emerald-500">Lens</span>
              <span className="ml-2 text-zinc-600">— demo data</span>
            </span>
            <span className="ml-auto text-[10px] text-zinc-600">
              <InlineMetadata
                parts={["NSE", "BSE", <SessionClock key="clock" reduced={reduced} />]}
              />
            </span>
          </div>
          <div className="h-[calc(100%-41px)]">
            <ExplorerTable tick={tick} />
          </div>
        </div>

        {/* Floating cards counter-shift slightly against the tilt. */}
        <div
          className="absolute inset-0 pointer-events-none transition-transform duration-300 ease-out"
          style={{ transform: `translate(${tilt.x * -6}px, ${tilt.y * -6}px)` }}
        >
          <DepthCard tick={tick} className="absolute -right-4 top-2 w-72 rotate-2" delay="0s" />
          <IncomeCard className="absolute -left-4 bottom-4 w-64 -rotate-2" delay="1.3s" />
        </div>
        <div
          className="absolute left-14 -top-3 rounded-lg border border-emerald-500/30 bg-zinc-900/90 px-3 py-1.5 text-xs shadow-xl"
          style={reduced ? undefined : { animation: "float-y 7s ease-in-out .6s infinite" }}
        >
          <span className="font-semibold text-zinc-100">AA-</span>
          <span className="ml-1.5 text-emerald-400">▲ Upgraded</span>
        </div>

        {!reduced && (
          <div
            className="absolute left-1/2 -translate-x-1/2 bottom-0 rounded-lg border border-zinc-700 bg-zinc-900/95 px-4 py-2 text-xs text-zinc-200 shadow-2xl whitespace-nowrap"
            style={{ animation: "toast-cycle 8s ease-in-out 2s infinite" }}
          >
            <span className="text-emerald-400">✓</span> Order placed — 51 units @ ₹99,750
          </div>
        )}
      </div>
    </div>
  );
}

/** Decorative live clock in the fake window chrome ("this is live"). */
function SessionClock({ reduced }: { reduced: boolean }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    if (reduced) return;
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, [reduced]);
  if (reduced) return <span>live session</span>;
  return <span>{now.toLocaleTimeString("en-IN", { hour12: false })} IST</span>;
}

function ExplorerTable({ tick }: { tick: number }) {
  return (
    <div className="px-5 text-[13px] h-full flex flex-col">
      <div className="grid grid-cols-[1.7fr_.7fr_1fr_.7fr_.9fr] gap-2 text-[10px] uppercase tracking-wide text-zinc-600 py-2.5 border-b border-zinc-800">
        <span>Symbol</span>
        <span className="text-right">YTM %</span>
        <span className="text-right">LTP</span>
        <span>Rating</span>
        <span className="text-right">Maturity</span>
      </div>
      {/* justify-around distributes the rows over whatever height the
          page gives the panel — no dead space at tall viewports. */}
      <div className="flex-1 flex flex-col justify-around">
        {ROWS.map(([sym, issuer, ytm, base, rating, mat, seed], i) => {
          // A few rows tick each cycle: price nudges, arrow follows the move.
          const w = wave(tick, seed);
          const moves = Math.abs(w) > 0.45;
          const delta = moves ? Math.round(w * 8) * 10 : 0;
          const price = base + delta;
          const up = delta >= 0;
          // One row's YTM drifts a couple of bps for life-likeness.
          const shownYtm =
            sym === "11HFC29" ? (Number(ytm) + wave(tick, 99) * 0.02).toFixed(2) : ytm;
          return (
            <div
              key={sym}
              style={rise(i)}
              className={`grid grid-cols-[1.7fr_.7fr_1fr_.7fr_.9fr] gap-2 items-center py-1.5 border-b border-zinc-900${
                i >= 6 ? " [@media(max-height:820px)]:hidden" : ""
              }`}
            >
              <span>
                <span className="font-medium text-zinc-200">{sym}</span>
                <span className="block text-[9px] text-zinc-600 truncate">{issuer}</span>
              </span>
              <span className="text-right font-semibold text-emerald-400">{shownYtm}%</span>
              <span
                key={price} /* remount on change so the flash replays */
                className="text-right text-zinc-300 rounded"
                style={moves ? { animation: "tick-flash 1.2s ease-out" } : undefined}
              >
                {inr(price)}
                <span className={up ? "text-emerald-500" : "text-red-400"}>{up ? " ▲" : " ▼"}</span>
              </span>
              <span className="text-zinc-300">
                {rating}
                {sym === "10MFI28" && (
                  <span className="ml-1.5 text-[8px] px-1 py-0.5 rounded bg-red-500/15 text-red-400 border border-red-500/30">
                    DOWNGRADED
                  </span>
                )}
              </span>
              <span className="text-right text-zinc-400">{mat}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DepthCard({ tick, className, delay }: { tick: number; className: string; delay: string }) {
  const maxQty = 140;
  const walk = (base: number, seed: number) =>
    Math.max(8, Math.round(base + wave(tick, seed) * base * 0.35));
  return (
    <div
      className={`${className} rounded-xl border border-zinc-700/80 bg-zinc-900/95 p-3 shadow-2xl`}
      style={{ animation: `float-y 6s ease-in-out ${delay} infinite` }}
    >
      <div className="flex items-center justify-between pb-2">
        <span className="text-[11px] font-medium text-zinc-300">
          9NBFC27 <span className="text-zinc-600">· depth</span>
        </span>
        <span className="inline-flex items-center gap-1 text-[9px] text-emerald-400 font-medium">
          <span className="relative flex h-1.5 w-1.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-60" />
            <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500" />
          </span>
          LIVE
        </span>
      </div>
      <div className="space-y-1">
        {BIDS.map(([price, qty], i) => {
          const q = walk(qty, i + 21);
          return (
            <div key={price} className="relative h-5">
              <div
                className="absolute inset-y-0.5 left-0 rounded-sm bg-emerald-500/15 transition-all duration-700"
                style={{ width: `${(q / maxQty) * 100}%` }}
              />
              <div className="relative flex justify-between text-[10px] leading-5">
                <span className="text-emerald-400 font-medium">{inr(price)}</span>
                <span className="text-zinc-400">{q}</span>
              </div>
            </div>
          );
        })}
        <div className="border-t border-zinc-800 my-1" />
        {ASKS.map(([price, qty], i) => {
          const q = walk(qty, i + 51);
          return (
            <div key={price} className="relative h-5">
              <div
                className="absolute inset-y-0.5 left-0 rounded-sm bg-red-500/15 transition-all duration-700"
                style={{ width: `${(q / maxQty) * 100}%` }}
              />
              <div className="relative flex justify-between text-[10px] leading-5">
                <span className="text-red-400 font-medium">{inr(price)}</span>
                <span className="text-zinc-400">{q}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function IncomeCard({ className, delay }: { className: string; delay: string }) {
  return (
    <div
      className={`${className} rounded-xl border border-zinc-700/80 bg-zinc-900/95 p-3 shadow-2xl`}
      style={{ animation: `float-y 6.8s ease-in-out ${delay} infinite` }}
    >
      <div className="flex items-baseline justify-between pb-2">
        <span className="text-[11px] font-medium text-zinc-300">Income to maturity</span>
        <span className="text-[11px] font-semibold text-emerald-400">₹3.2 L</span>
      </div>
      <div className="flex items-end gap-1 h-16">
        {INCOME_BARS.map((h, i) => (
          <div key={`${i}-${h}`} className="flex-1 flex flex-col justify-end h-full">
            <div
              style={{
                height: `${h * 100}%`,
                animation: "grow-bar .6s ease-out both",
                animationDelay: `${300 + i * 80}ms`,
                transformOrigin: "bottom",
              }}
              className={
                h === 1
                  ? "rounded-sm bg-zinc-500/70"
                  : "rounded-sm bg-gradient-to-t from-emerald-600/80 to-emerald-400/80"
              }
            />
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[9px] text-zinc-600">
        <span>monthly interest</span>
        <span>↑ principal</span>
      </div>
    </div>
  );
}
