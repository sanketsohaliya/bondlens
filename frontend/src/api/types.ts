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

// Hand-added pending codegen (backend models exist; run `npm run codegen`
// against a live API to fold these into ./generated).
export type {
  DepthLevel,
  DepthSide,
  Notification,
  NotificationsResponse,
  OrderBook,
  PortfolioLot,
  PortfolioMonth,
  PortfolioMonthBond,
  PortfolioNews,
  PortfolioPosition,
  PortfolioResponse,
  PortfolioSummary,
  Watchlist,
  YtmAtPrice,
} from "./generated/types.gen";

// Lot create/update request bodies (hand-maintained).
export interface LotInput {
  units: number;
  price?: number | null;
  purchase_date?: string | null;
  platform?: string | null;
}

// Explorer query params (request side; hand-maintained).
export interface ExploreParams {
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
  isins?: string;
  q?: string;
  sort?: string;
  dir?: "asc" | "desc";
  limit?: number;
}
