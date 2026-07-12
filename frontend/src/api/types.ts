// Response types are GENERATED from the backend's OpenAPI schema —
// run `npm run codegen` (API must be up) after changing bondlens/models.py.
export type {
  Bond,
  BondDetail,
  BondsResponse,
  Cashflow,
  Filters,
  IssuerBond,
  RatingEvent,
  Status,
  Trade,
  TradesResponse,
  WatchlistEntry,
} from "./generated";

// Screener query params (request side; hand-maintained).
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
