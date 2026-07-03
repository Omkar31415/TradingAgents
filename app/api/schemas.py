"""Request/response schemas for the assistant API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AddTickerRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32, description="Yahoo Finance symbol, e.g. NVDA, RELIANCE.NS, BTC-USD")


class WatchlistItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    market: str
    asset_type: str
    tier: str
    added_by: str
    consecutive_holds: int
    last_rating: str | None
    last_run_at: datetime | None
    note: str | None


class SignalItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    market: str
    trade_date: str
    rating: str | None
    prev_rating: str | None
    changed: bool
    status: str
    error: str | None
    report_path: str | None
    duration_seconds: float | None
    created_at: datetime


class RunTriggeredResponse(BaseModel):
    market: str
    status: str = "started"


class ScheduleSlotItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    run_time: str
    timezone: str
    market: str | None
    enabled: bool
    max_tickers: int


class ScheduleSlotCreate(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    run_time: str = Field(pattern=r"^([01]?\d|2[0-3]):[0-5]\d$")
    timezone: str = "America/Chicago"
    market: str | None = Field(default=None, description="us / india / crypto, or null for any")
    enabled: bool = True
    max_tickers: int = Field(default=1, ge=1, le=20)


class ScheduleSlotUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=64)
    run_time: str | None = Field(default=None, pattern=r"^([01]?\d|2[0-3]):[0-5]\d$")
    timezone: str | None = None
    market: str | None = Field(
        default=None, description="us / india / crypto, or 'any' to clear the filter"
    )
    enabled: bool | None = None
    max_tickers: int | None = Field(default=None, ge=1, le=20)


class PriceHistory(BaseModel):
    symbol: str
    dates: list[str]
    close: list[float]


class ReportVersionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trade_date: str
    rating: str | None
    created_at: datetime


class ReportResponse(BaseModel):
    signal_id: int
    symbol: str
    trade_date: str
    rating: str | None
    created_at: datetime
    markdown: str


class PositionItem(BaseModel):
    id: int
    account_type: str
    symbol: str
    market: str
    currency: str
    quantity: float
    avg_price: float
    stop_loss: float | None
    price_target: float | None
    opened_at: datetime
    note: str | None
    # Live valuation (None when a quote is unavailable)
    last_price: float | None
    value_usd: float | None
    pnl_usd: float | None
    pnl_pct: float | None


class PortfolioResponse(BaseModel):
    cash_usd: float
    starting_cash_usd: float
    equity_usd: float | None
    return_pct: float | None
    benchmark_return_pct: float | None  # SPY over the same period
    paper_positions: list[PositionItem]
    real_positions: list[PositionItem]


class AddHoldingRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    quantity: float = Field(gt=0)
    price: float = Field(gt=0, description="Average buy price in the instrument's own currency")
    bought_at: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD"
    )
    stop_loss: float | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=200)


class TradeItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_type: str
    symbol: str
    side: str
    quantity: float
    price: float
    currency: str
    reason: str
    realized_pnl_usd: float | None
    executed_at: datetime


class ScreenerResultItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_date: str
    symbol: str
    market: str
    score: float
    summary: str
    added: bool
    created_at: datetime


class HealthResponse(BaseModel):
    status: str
    scheduler_running: bool
    jobs: list[dict]
    telegram_configured: bool
    email_configured: bool
    llm_provider: str
    deep_model: str
    quick_model: str
    runs_today: int
    daily_run_budget: int
