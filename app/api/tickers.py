"""Per-ticker data for the dashboard: price history and the latest analysis report."""

import asyncio
import logging
import math
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import PriceHistory, ReportResponse
from app.models.base import get_session
from app.repositories.signals import SignalRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tickers", tags=["tickers"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _fetch_prices_sync(symbol: str, days: int) -> tuple[list[str], list[float]]:
    from datetime import date, timedelta

    import yfinance as yf

    from tradingagents.dataflows.symbol_utils import normalize_symbol

    # start= keeps ranges in calendar days ("1M" means one month); a period
    # string like "30d" would mean 30 *trading* days (~6 weeks).
    start = (date.today() - timedelta(days=days)).isoformat()
    history = yf.Ticker(normalize_symbol(symbol)).history(start=start)
    if history.empty:
        return [], []
    dates = [d.strftime("%Y-%m-%d") for d in history.index]
    close = [round(float(c), 4) for c in history["Close"]]
    # Drop NaN rows (halts, listing gaps) so the chart doesn't break.
    pairs = [(d, c) for d, c in zip(dates, close, strict=True) if not math.isnan(c)]
    return [p[0] for p in pairs], [p[1] for p in pairs]


@router.get("/{symbol}/prices", response_model=PriceHistory)
async def price_history(
    symbol: str,
    days: Annotated[int, Query(ge=7, le=730)] = 180,
) -> PriceHistory:
    dates, close = await asyncio.to_thread(_fetch_prices_sync, symbol, days)
    if not dates:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No price data for {symbol.upper()}",
        )
    return PriceHistory(symbol=symbol.upper(), dates=dates, close=close)


@router.get("/{symbol}/report", response_model=ReportResponse)
async def latest_report(symbol: str, session: SessionDep) -> ReportResponse:
    """The most recent successful analysis report for a ticker."""
    record = await SignalRepository(session).latest_success(symbol)
    if record is None or not record.report_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No analysis on record for {symbol.upper()} yet",
        )
    report_file = Path(record.report_path)
    if report_file.is_dir():
        report_file = report_file / "complete_report.md"
    if not report_file.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report file missing on disk (was it moved or deleted?)",
        )
    markdown = await asyncio.to_thread(report_file.read_text, "utf-8")
    return ReportResponse(
        symbol=record.symbol,
        trade_date=record.trade_date,
        rating=record.rating,
        markdown=markdown,
    )
