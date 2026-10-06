// Response types are GENERATED from the backend's OpenAPI schema —
// run `npm run codegen` (API must be up) after changing bondlens/models.py.
export type {
  AlertRule,
  AlertRuleIn,
  AlertRulePatch,
  AlertSettings,
  AlertsOverview,
  AskYtm,
  AskYtmResponse,
  Bond,
  BondDetail,
  BondsResponse,
  Cashflow,
  CashflowEvidenceResponse,
  DepthLevel,
  DepthSide,
  EvidenceAvailability,
  EvidenceFieldComparison,
  EvidenceSummary,
  ExchangeNotice,
  ExitTarget,
  ExitTargetIn,
  Filters,
  FlowEvidence,
  FlowSourceEvidence,
  IssuerBond,
  KiteMargins,
  KiteOrder,
  KiteOrderId,
  KiteOrderModify,
  KiteOrderRequest,
  KiteOrdersResponse,
  KiteStatus,
  ListsResponse,
  Notification,
  NotificationsResponse,
  OpportunityHit,
  OrderBook,
  PortfolioIncome,
  PortfolioLot,
  PortfolioMonth,
  PortfolioMonthBond,
  PortfolioNews,
  PortfolioPosition,
  PortfolioResponse,
  PortfolioSummary,
  RatingEvent,
  Status,
  Trade,
  TradesResponse,
  User,
  Watchlist,
  YieldValuation,
  YieldWarning,
  YtmAtPrice,
} from "./generated";

// Lot create/update request bodies (hand-maintained).
// Units, price and purchase date are hard requirements (API rejects
// them missing; the DB columns are NOT NULL).
export interface LotInput {
  units: number;
  price: number;
  purchase_date: string;
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
