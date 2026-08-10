import { useEffect, useState } from "react";

/* Animated product showcase for the sign-in page: three scenes (explorer,
   portfolio, order book) rotating with a crossfade. Everything below is
   FICTIONAL demo data — invented issuers, symbols and numbers — rendered
   in code so it stays crisp, weightless and on-theme. */

const SCENE_MS = 4500;

const CAPTIONS = [
  "2,000+ listed NCDs with live YTM, ratings and liquidity",
  "Your holdings, income mapped to maturity",
  "Live order-book depth, straight from your broker",
];

// -- Scene 1: explorer rows (fictional) --------------------------------------
const EXPLORER_ROWS = [
  ["9NBFC27", "NBFC FINANCE LTD", "11.84", "₹1,01,250", "up", "AA-", "Jun 2027"],
  ["11HFC29", "HOMEFIRST HOUSING", "12.40", "₹98,730", "up", "A+", "Mar 2029"],
  ["10MFI28", "GRAMEEN MICROFIN", "13.15", "₹99,910", "down", "A", "Sep 2028"],
  ["9IFRA31", "INFRA INVIT TRUST", "10.62", "₹1,04,080", "up", "AAA", "Dec 2031"],
  ["12GOLD26", "AURUM GOLD LOANS", "12.95", "₹1,00,340", "up", "A-", "Nov 2026"],
  ["10SFB28", "SARVODAYA SFB", "11.20", "₹99,450", "down", "AA-", "Feb 2028"],
] as const;

// -- Scene 2: portfolio (fictional) -------------------------------------------
const STATS = [
  ["Invested", "₹4.2 L", false],
  ["Current value", "₹4.4 L (+4.7%)", true],
  ["Avg YTM", "12.4%", true],
] as const;
// Monthly income bars: relative heights 0..1; one redemption spike.
const BARS = [0.3, 0.3, 0.32, 0.3, 0.55, 0.3, 0.32, 0.3, 0.3, 1.0, 0.18, 0.2];
const REDEMPTION_BAR = 9;

// -- Scene 3: order book (fictional) ------------------------------------------
const BIDS = [
  ["₹99,750", 51],
  ["₹99,400", 120],
  ["₹99,100", 30],
  ["₹98,800", 75],
  ["₹98,500", 12],
] as const;
const ASKS = [
  ["₹1,00,150", 40],
  ["₹1,00,400", 90],
  ["₹1,00,900", 25],
  ["₹1,01,200", 60],
  ["₹1,01,500", 18],
] as const;

const rise = (i: number): React.CSSProperties => ({
  animation: `rise-in .5s ease-out both`,
  animationDelay: `${i * 90}ms`,
});

export default function LoginShowcase() {
  const [scene, setScene] = useState(0);
  const [reduced] = useState(
    () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
  );

  useEffect(() => {
    if (reduced) return;
    const t = setInterval(() => setScene((s) => (s + 1) % 3), SCENE_MS);
    return () => clearInterval(t);
  }, [reduced]);

  return (
    <div className="select-none">
      <h2 className="text-4xl font-extrabold tracking-tight leading-tight text-zinc-100">
        Every listed corporate bond.
        <br />
        <span className="text-emerald-400">One lens.</span>
      </h2>
      <p className="mt-3 h-6 text-sm text-zinc-400 transition-opacity duration-500" key={scene}>
        {CAPTIONS[scene]}
      </p>

      <div className="mt-6 rounded-2xl border border-zinc-800/80 bg-zinc-900/40 backdrop-blur-sm shadow-[0_0_80px_-20px_rgba(16,185,129,0.25)] overflow-hidden">
        {/* fake window chrome */}
        <div className="flex items-center gap-1.5 px-4 py-2.5 border-b border-zinc-800/80 bg-zinc-900/60">
          <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
          <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
          <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
          <span className="ml-3 text-[11px] text-zinc-500 font-medium">
            Bond<span className="text-emerald-500">Lens</span>
            <span className="ml-2 text-zinc-600">— demo data</span>
          </span>
        </div>

        <div className="relative h-80">
          {[<ExplorerScene key="e" />, <PortfolioScene key="p" />, <DepthScene key="d" />].map(
            (el, i) => (
              <div
                key={CAPTIONS[i]}
                className={`absolute inset-0 p-5 transition-all duration-700 ${
                  scene === i ? "opacity-100 translate-y-0" : "opacity-0 translate-y-2"
                }`}
              >
                {/* remount the active scene so entrance animations replay */}
                {scene === i || reduced ? el : null}
              </div>
            ),
          )}
        </div>
      </div>

      <div className="mt-4 flex items-center gap-2">
        {CAPTIONS.map((c, i) => (
          <button
            key={c}
            type="button"
            aria-label={`Show scene ${i + 1}`}
            onClick={() => setScene(i)}
            className={`h-1.5 rounded-full transition-all duration-300 ${
              scene === i ? "w-6 bg-emerald-500" : "w-1.5 bg-zinc-700 hover:bg-zinc-500"
            }`}
          />
        ))}
      </div>
    </div>
  );
}

function ExplorerScene() {
  return (
    <div className="text-xs">
      <div className="grid grid-cols-[1.6fr_.7fr_1fr_.7fr_.8fr] gap-2 text-[10px] uppercase tracking-wide text-zinc-600 pb-2 border-b border-zinc-800">
        <span>Symbol</span>
        <span className="text-right">YTM %</span>
        <span className="text-right">LTP</span>
        <span>Rating</span>
        <span className="text-right">Maturity</span>
      </div>
      {EXPLORER_ROWS.map(([sym, issuer, ytm, ltp, dir, rating, mat], i) => (
        <div
          key={sym}
          style={rise(i)}
          className="grid grid-cols-[1.6fr_.7fr_1fr_.7fr_.8fr] gap-2 items-center py-2 border-b border-zinc-900"
        >
          <span>
            <span className="font-medium text-zinc-200">{sym}</span>
            <span className="block text-[9px] text-zinc-600 truncate">{issuer}</span>
          </span>
          <span className="text-right font-semibold text-emerald-400">{ytm}%</span>
          <span className="text-right text-zinc-300">
            {ltp}
            <span className={dir === "up" ? "text-emerald-500" : "text-red-400"}>
              {dir === "up" ? " ▲" : " ▼"}
            </span>
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
      ))}
    </div>
  );
}

function PortfolioScene() {
  return (
    <div>
      <div className="grid grid-cols-3 gap-3">
        {STATS.map(([label, value, accent], i) => (
          <div
            key={label}
            style={rise(i)}
            className="rounded-lg border border-zinc-800 bg-zinc-900/50 px-3 py-2"
          >
            <div className="text-[10px] text-zinc-500">{label}</div>
            <div
              className={`text-base font-semibold ${accent ? "text-emerald-400" : "text-zinc-100"}`}
            >
              {value}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-4 text-[10px] uppercase tracking-wide text-zinc-600">
        Income to maturity
      </div>
      <div className="mt-2 flex items-end gap-2 h-40">
        {BARS.map((h, i) => (
          <div key={`${i}-${h}`} className="flex-1 flex flex-col justify-end h-full">
            <div
              style={{
                height: `${h * 100}%`,
                animation: "grow-bar .6s ease-out both",
                animationDelay: `${200 + i * 70}ms`,
                transformOrigin: "bottom",
              }}
              className={
                i === REDEMPTION_BAR
                  ? "rounded-sm bg-zinc-500/70"
                  : "rounded-sm bg-gradient-to-t from-emerald-600/80 to-emerald-400/80"
              }
            />
          </div>
        ))}
      </div>
      <div className="mt-1.5 flex justify-between text-[9px] text-zinc-600">
        <span>Sep 26</span>
        <span className="text-zinc-500">↑ principal back</span>
        <span>Aug 27</span>
      </div>
    </div>
  );
}

function DepthScene() {
  const maxQty = 120;
  return (
    <div className="text-xs">
      <div className="flex items-center justify-between pb-2">
        <span className="text-zinc-300 font-medium">
          9NBFC27 <span className="text-zinc-600">· BSE</span>
        </span>
        <span className="inline-flex items-center gap-1.5 text-[10px] text-emerald-400 font-medium">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-60" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
          </span>
          LIVE
        </span>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="text-[10px] uppercase tracking-wide text-zinc-600 pb-1.5">Buyers</div>
          {BIDS.map(([price, qty], i) => (
            <div key={price} style={rise(i)} className="relative py-1.5 border-b border-zinc-900">
              <div
                className="absolute inset-y-1 right-0 rounded-sm bg-emerald-500/10"
                style={{ width: `${(qty / maxQty) * 100}%` }}
              />
              <div className="relative flex justify-between">
                <span className="font-medium text-emerald-400">{price}</span>
                <span className="text-zinc-400">{qty}</span>
              </div>
            </div>
          ))}
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide text-zinc-600 pb-1.5">Sellers</div>
          {ASKS.map(([price, qty], i) => (
            <div
              key={price}
              style={{
                ...rise(i),
                ...(i === 0 ? { animation: "tick-flash 2.4s ease-in-out 1s infinite" } : {}),
              }}
              className="relative py-1.5 border-b border-zinc-900"
            >
              <div
                className="absolute inset-y-1 left-0 rounded-sm bg-red-500/10"
                style={{ width: `${(qty / maxQty) * 100}%` }}
              />
              <div className="relative flex justify-between">
                <span className="font-medium text-red-400">{price}</span>
                <span className="text-zinc-400">{qty}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className="mt-3 flex justify-between text-[10px] text-zinc-500">
        <span>
          YTM at best ask: <span className="text-zinc-200 font-medium">11.9%</span>
        </span>
        <span>
          One click: <span className="text-emerald-400">Buy</span> ·{" "}
          <span className="text-red-400">Sell</span>
        </span>
      </div>
    </div>
  );
}
