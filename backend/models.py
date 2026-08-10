"""Pydantic response models — the API contract.

Single source of truth for response shapes: FastAPI validates every
response against these and emits the OpenAPI schema the frontend's
TypeScript client is generated from (`npm run codegen` in frontend/).
"""
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

YtmMethod = Literal["exact", "estimated", "approx", "ytc"]


class Bond(BaseModel):
    """One explorer row (bond_analytics + read-time derived fields)."""
    isin: str
    issuer_name: str | None
    sector: str | None
    industry: str | None
    on_nse: bool
    on_bse: bool
    nse_symbol: str | None
    nse_series: str | None
    bse_scrip_id: str | None
    coupon: float | None
    face_value: float | None
    coupon_basis: str | None
    frequency: str | None
    secured: bool | None
    tax_free: bool | None
    perpetual: bool
    maturity_date: date | None
    years_to_maturity: float | None
    schedule_shape: str
    ltp: float | None
    trade_date: date | None
    exchange: str | None
    volume: float | None
    turnover: float | None
    num_trades: int | None
    nse_turnover: float | None
    bse_turnover: float | None
    prev_turnover: float | None
    prev_ltp: float | None
    days_since_traded: int | None
    stale: bool
    otc_price_pct: float | None
    otc_yield: float | None
    otc_trade_date: date | None
    otc_days_since_traded: int | None
    otc_turnover: float | None
    ytm: float | None
    ytm_method: YtmMethod | None
    ytm_reliable: bool | None
    current_yield: float | None
    duration: float | None
    credit_rating: str | None
    rating_rank: int | None
    rating_change: Literal["up", "down"] | None
    rating_prev: str | None
    rating_changed_at: date | None
    in_default: bool
    tech_delay: bool
    computed_at: datetime


class BondsResponse(BaseModel):
    count: int
    bonds: list[Bond]
    as_of: float | None


class Filters(BaseModel):
    sectors: list[str]
    frequencies: list[str]
    ratings: list[str]


class Cashflow(BaseModel):
    event: Literal["interest", "redemption"]
    due_date: date
    record_date: date | None
    amount: float | None
    estimated: bool
    future: bool


class RatingEvent(BaseModel):
    rating: str | None
    outlook: str | None
    action: str | None
    rating_date: date | None
    press_release: str | None


class Document(BaseModel):
    doc_name: str | None
    source: str | None
    download_link: str | None


class IssuerBond(BaseModel):
    isin: str
    nse_symbol: str | None
    nse_series: str | None
    coupon_rate: float | None
    face_value: float | None
    redemption_date: date | None
    trade_date: date | None
    close: float | None
    turnover: float | None


class BondDetail(BaseModel):
    # Identity (bonds x bond_info)
    isin: str
    status: str
    on_nse: bool
    on_bse: bool
    bse_status: str | None
    issuer_name: str | None
    nse_symbol: str | None
    nse_series: str | None
    bse_scrip_code: str | None
    bse_scrip_id: str | None
    sector: str | None
    industry: str | None
    basic_industry: str | None
    macro: str | None
    cin: str | None
    lei: str | None
    sec_type_desc: str | None
    instrument_type: str | None
    first_seen: datetime | None
    last_seen: datetime | None
    # Terms (full bond_terms row; ragged NSDL columns stay untyped)
    terms: dict[str, Any] | None
    trustee_name: str | None
    kite_url: str | None
    # Pricing
    ltp: float | None
    custom_price: bool
    trade_date: date | None
    exchange: str | None
    days_since_traded: int | None
    volume: float | None
    turnover: float | None
    num_trades: int | None
    otc_price_pct: float | None
    otc_yield: float | None
    otc_trade_date: date | None
    coupon: float | None
    implied_coupon: float | None
    face_value: float | None
    frequency: str | None
    # Schedule provenance
    schedule_shape: str
    schedule_usable: bool
    schedule_warnings: list[str]
    amounts_estimated: bool
    issue_date: date | None
    maturity_date: date | None
    years_to_maturity: float | None
    # Yields
    ytm_exact: float | None
    ytm_approx: float | None
    ytm: float | None
    yield_to_worst: float | None
    call_dates: list[date]
    current_yield: float | None
    accrued: float | None
    clean_price: float | None
    duration: float | None
    # Ratings
    credit_rating: str | None
    rating_rank: int | None
    in_default: bool
    tech_delay: bool
    rating_timeline: dict[str, list[RatingEvent]]
    # Cashflows
    cashflows: list[Cashflow]
    future_total_per_unit: float
    next_payment: Cashflow | None
    # Related records (ragged NSDL shapes stay untyped)
    listings: list[dict[str, Any]]
    documents: list[Document]
    further_issues: list[dict[str, Any]]
    redemption_events: list[dict[str, Any]]
    defaults: list[dict[str, Any]]
    technical_defaults: list[dict[str, Any]]
    issuer_bonds: list[IssuerBond]


class Trade(BaseModel):
    trade_date: date
    exchange: Literal["NSE", "BSE"]
    segment: Literal["retail", "otc"]
    price_basis: str | None
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    last_price: float | None
    wap: float | None
    last_yield: float | None
    wap_yield: float | None
    volume: float | None
    turnover: float | None
    num_trades: int | None


class TradesResponse(BaseModel):
    isin: str
    count: int
    trades: list[Trade]


class User(BaseModel):
    id: int
    email: str
    name: str | None
    picture: str | None


class Watchlist(BaseModel):
    id: int
    name: str
    isins: list[str]


class ListsResponse(BaseModel):
    holdings: list[str]
    watchlists: list[Watchlist]


class WatchlistName(BaseModel):
    name: str


class WatchlistToggle(BaseModel):
    watchlist_id: int
    isin: str
    present: bool


class PortfolioLot(BaseModel):
    id: int
    isin: str
    units: float
    price: float | None
    purchase_date: date | None
    platform: str | None


class LotCreate(BaseModel):
    isin: str
    units: float
    price: float | None = None
    purchase_date: date | None = None
    platform: str | None = None


class LotUpdate(BaseModel):
    units: float
    price: float | None = None
    purchase_date: date | None = None
    platform: str | None = None


class PortfolioPosition(BaseModel):
    isin: str
    issuer_name: str | None
    credit_rating: str | None
    rating_rank: int | None
    rating_grade: str | None
    rating_change: Literal["up", "down"] | None
    rating_prev: str | None
    rating_changed_at: date | None
    sector: str | None
    maturity_date: date | None
    years_to_maturity: float | None
    face_value: float | None
    ltp: float | None
    ytm: float | None
    units: float
    invested: float | None
    value: float | None
    gain: float | None
    gain_pct: float | None
    lots: list[PortfolioLot]
    next_payment: Cashflow | None  # amount is x units (position-level)


class PortfolioSummary(BaseModel):
    invested: float
    value: float
    gain: float | None
    weighted_ytm: float | None
    weighted_years: float | None
    issuers: int
    bonds: int


class PortfolioMonthBond(BaseModel):
    isin: str
    label: str  # display symbol (nse/bse) or the ISIN itself
    dates: list[str] = []  # ISO due dates of this bond's flows in the month
    interest: float
    principal: float
    estimated: bool


class PortfolioMonth(BaseModel):
    month: str  # "YYYY-MM"
    interest: float    # month total (received + projected)
    principal: float
    received_interest: float    # past-due portion (holder semantics,
    received_principal: float   # lots with known purchase dates only)
    estimated: bool
    bonds: list[PortfolioMonthBond]  # largest payer first


class PortfolioIncome(BaseModel):
    """Received-so-far vs projected, summed from the month buckets."""
    received_interest: float
    received_principal: float
    projected_interest: float
    projected_principal: float


class PortfolioNews(BaseModel):
    isin: str
    issuer_name: str | None
    agency: str | None
    rating: str | None
    action: str | None
    rating_date: date | None
    press_release: str | None


class PortfolioResponse(BaseModel):
    summary: PortfolioSummary
    positions: list[PortfolioPosition]
    cashflows: list[PortfolioMonth]
    income: PortfolioIncome
    news: list[PortfolioNews]


class Notification(BaseModel):
    id: int
    kind: str       # rating | pay_upcoming | pay_today | pay_confirmed
                    # | pay_overdue | default | anomaly
    severity: str   # info | warn | critical
    isin: str
    title: str
    body: str
    created_at: datetime
    read_at: datetime | None


class NotificationsResponse(BaseModel):
    items: list[Notification]
    unread: int


class Ok(BaseModel):
    ok: bool


class RunInfo(BaseModel):
    """Latest job-run summary (subset shared by bond_runs / nsdl_runs)."""
    run_at: datetime
    ok: bool | None
    total: int | None = None
    added: int | None = None
    dropped: int | None = None
    finished_at: datetime | None = None
    fetched: int | None = None
    failed: int | None = None


class Status(BaseModel):
    universe_run: RunInfo | None
    nsdl_run: RunInfo | None
    eod_sources: dict[str, date]
    latest_trade_date: date | None
    bonds_listed: int
    bonds_total: int


class DepthLevel(BaseModel):
    price: float | None
    qty: float | None
    orders: int | None = None


class DepthSide(BaseModel):
    bids: list[DepthLevel]
    asks: list[DepthLevel]
    total_bid_qty: float | None
    total_ask_qty: float | None
    as_of: str | None
    source: Literal["kite", "exchange"] | None = None


class YtmAtPrice(BaseModel):
    price: float
    ytm: float | None


class OrderBook(BaseModel):
    isin: str
    fetched_at: str
    nse: DepthSide | None
    bse: DepthSide | None
    ytm_at_ask: list[YtmAtPrice]


class KiteStatus(BaseModel):
    """Kite Connect state for the current user."""
    configured: bool
    connected: bool
    login_url: str | None


class KiteOrderRequest(BaseModel):
    isin: str
    exchange: Literal["NSE", "BSE"]
    transaction_type: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)


class KiteOrderModify(BaseModel):
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)


class KiteMargins(BaseModel):
    """Equity-segment funds snapshot from Kite."""
    net: float | None
    available_cash: float | None
    available_margin: float | None
    margin_used: float | None


class KiteOrderId(BaseModel):
    order_id: str


class KiteOrder(BaseModel):
    order_id: str
    isin: str | None = None  # resolved from the tradingsymbol; None = not a bond
    issuer_name: str | None = None
    tradingsymbol: str | None
    exchange: str | None
    transaction_type: str | None
    quantity: int | None
    filled_quantity: int | None
    price: float | None
    average_price: float | None
    status: str | None
    status_message: str | None
    order_timestamp: str | None


class KiteOrdersResponse(BaseModel):
    orders: list[KiteOrder]


class Health(BaseModel):
    app: str
    version: str
