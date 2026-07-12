// Typed mirrors of the FastAPI response payloads.

export interface Bond {
  isin: string;
  issuer_name: string | null;
  sector: string | null;
  industry: string | null;
  on_nse: boolean;
  on_bse: boolean;
  nse_symbol: string | null;
  nse_series: string | null;
  bse_scrip_id: string | null;
  coupon: number | null;
  face_value: number | null;
  coupon_basis: string | null;
  frequency: string | null;
  secured: boolean | null;
  tax_free: boolean | null;
  perpetual: boolean;
  maturity_date: string | null;
  years_to_maturity: number | null;
  schedule_shape: string;
  ltp: number | null;
  trade_date: string | null;
  exchange: string | null;
  volume: number | null;
  turnover: number | null;
  num_trades: number | null;
  days_since_traded: number | null;
  stale: boolean;
  otc_price_pct: number | null;
  otc_yield: number | null;
  otc_trade_date: string | null;
  otc_days_since_traded: number | null;
  ytm: number | null;
  ytm_method: "exact" | "estimated" | "approx" | null;
  current_yield: number | null;
  duration: number | null;
  credit_rating: string | null;
  rating_rank: number | null;
  rating_change: "up" | "down" | null;
  rating_prev: string | null;
  rating_changed_at: string | null;
  in_default: boolean;
  in_watchlist: boolean;
}

export interface BondsResponse {
  count: number;
  bonds: Bond[];
  as_of: number;
}

export interface Cashflow {
  event: "interest" | "redemption";
  due_date: string;
  record_date: string | null;
  amount: number | null;
  estimated: boolean;
  future: boolean;
}

export interface RatingEvent {
  rating: string | null;
  outlook: string | null;
  action: string | null;
  rating_date: string | null;
  press_release: string | null;
}

export interface IssuerBond {
  isin: string;
  nse_symbol: string | null;
  coupon_rate: number | null;
  face_value: number | null;
  redemption_date: string | null;
  trade_date: string | null;
  close: number | null;
  turnover: number | null;
}

export interface BondDetail extends Omit<Bond, "schedule_shape"> {
  terms: Record<string, unknown> | null;
  kite_url: string | null;
  custom_price: boolean;
  schedule_shape: string;
  schedule_usable: boolean;
  schedule_warnings: string[];
  amounts_estimated: boolean;
  ytm_exact: number | null;
  ytm_approx: number | null;
  yield_to_worst: number | null;
  call_dates: string[];
  accrued: number | null;
  clean_price: number | null;
  rating_timeline: Record<string, RatingEvent[]>;
  cashflows: Cashflow[];
  future_total_per_unit: number;
  next_payment: Cashflow | null;
  listings: Record<string, unknown>[];
  documents: { doc_name: string | null; download_link: string | null }[];
  further_issues: Record<string, unknown>[];
  redemption_events: Record<string, unknown>[];
  defaults: Record<string, unknown>[];
  technical_defaults: Record<string, unknown>[];
  issuer_bonds: IssuerBond[];
}

export interface Trade {
  trade_date: string;
  exchange: "NSE" | "BSE";
  segment: "retail" | "otc";
  price_basis: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  last_price: number | null;
  wap: number | null;
  last_yield: number | null;
  wap_yield: number | null;
  volume: number | null;
  turnover: number | null;
  num_trades: number | null;
}

export interface TradesResponse {
  isin: string;
  count: number;
  trades: Trade[];
}

export interface Status {
  universe_run: { run_at: string; ok: boolean; total: number } | null;
  nsdl_run: { run_at: string; ok: boolean | null; fetched: number } | null;
  eod_sources: Record<string, string>;
  latest_trade_date: string | null;
  bonds_listed: number;
  bonds_total: number;
}

export interface Filters {
  sectors: string[];
  frequencies: string[];
  ratings: string[];
}

export interface ScreenerParams {
  min_ytm?: number;
  max_ytm?: number;
  min_rating?: string;
  sector?: string;
  exchange?: string;
  frequency?: string;
  min_years?: number;
  max_years?: number;
  max_ltp?: number;
  min_turnover?: number;
  secured_only?: boolean;
  tax_free_only?: boolean;
  below_par?: boolean;
  fresh_only?: boolean;
  exact_only?: boolean;
  downgraded_only?: boolean;
  exclude_defaulted?: boolean;
  watchlist_only?: boolean;
  q?: string;
  limit?: number;
}
