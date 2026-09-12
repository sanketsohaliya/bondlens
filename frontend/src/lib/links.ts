/** External destinations shared by the Explore rows and the bond drawer. */

export const CHATGPT_URL = "https://chatgpt.com/";

export function nsdlUrl(isin: string): string {
  return `https://www.indiabondinfo.nsdl.com/CBDServices/#/instrumentDetails/${encodeURIComponent(isin)}`;
}

export const LINK_TIPS = {
  nsdl: "Official bond record on NSDL (issuer filings, cashflow schedule)",
  kite: "Open the price chart on Zerodha Kite",
  askAi: "Opens ChatGPT with this bond's full brief copied to your clipboard — just paste.",
  askAiBlocked: "Copied — open chatgpt.com and paste",
  askAiFailed: "Couldn't load bond details — try again",
} as const;
