// Response types are GENERATED from the backend's OpenAPI schema —
// run `npm run codegen` (API must be up) after changing bondlens/models.py.
export type {
  Bond,
  BondDetail,
  BondsResponse,
  Cashflow,
  Filters,
  IssuerBond,
  ListsResponse,
  RatingEvent,
  Status,
  Trade,
  TradesResponse,
  User,
} from "./generated";

export type ListName = "watchlist" | "holdings";

// Screener query params (request side; hand-maintained).
export interface ScreenerParams {
  min_ytm?: number;
  max_ytm?: number;
  min_rating?: string;
  sector?: string;
  exchange?: string;
  frequency?: string;
  payout?: string;
  min_years?: number;
  max_years?: number;
  max_ltp?: number;
  min_turnover?: number;
  min_otc_turnover?: number;
  retail_only?: boolean;
  otc_only?: boolean;
  secured_only?: boolean;
  unsecured_only?: boolean;
  tax_free_only?: boolean;
  below_par?: boolean;
  fresh_only?: boolean;
  traded_within?: number;
  exact_only?: boolean;
  downgraded_only?: boolean;
  exclude_defaulted?: boolean;
  isins?: string;
  q?: string;
  limit?: number;
}
