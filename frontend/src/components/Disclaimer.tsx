import { useState } from "react";

/* Two-tier disclaimer shared by the sign-in page and the app footer:
   one short line, full text a click away. Single source of truth for
   the wording. */

const FULL_TEXT =
  "Not investment advice. BondLens is an analytics tool, not a broker, " +
  "exchange, or SEBI-registered investment adviser or research analyst. " +
  "Prices, yields and ratings are derived from NSE, BSE and NSDL data that " +
  "may be delayed, incomplete or inaccurate — verify independently before " +
  "acting. Orders placed through BondLens execute on your own Zerodha " +
  "account under Zerodha's terms, brokerage and risk checks; BondLens " +
  "never holds your funds or securities. Bonds carry credit and liquidity " +
  "risk, including loss of principal.";

export default function Disclaimer() {
  const [open, setOpen] = useState(false);
  return (
    <div className="max-w-4xl mx-auto text-center text-xs leading-relaxed text-zinc-500">
      <p>
        Not investment advice · not a SEBI-registered adviser ·{" "}
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          className="text-zinc-400 hover:text-zinc-200 underline decoration-dotted underline-offset-2"
        >
          Full disclaimer {open ? "▴" : "▾"}
        </button>
      </p>
      {open && <p className="mt-2 text-zinc-600">{FULL_TEXT}</p>}
    </div>
  );
}
