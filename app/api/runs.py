"""Run history and manual run triggers."""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import RunTriggeredResponse, SignalItem
from app.domain import Market
from app.models.base import get_session
from app.repositories.signals import SignalRepository
from app.services.pipeline import run_market

logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs"])

# Keep strong references so fire-and-forget run tasks aren't garbage-collected
# mid-flight (asyncio only holds weak refs to tasks).
_background_runs: set[asyncio.Task] = set()


@router.get("/signals", response_model=list[SignalItem])
async def list_signals(
    session: Annotated[AsyncSession, Depends(get_session)],
    symbol: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[SignalItem]:
    records = await SignalRepository(session).list_recent(symbol=symbol, limit=limit)
    return [SignalItem.model_validate(r) for r in records]


@router.post("/runs/{market}", response_model=RunTriggeredResponse, status_code=202)
async def trigger_run(market: Market) -> RunTriggeredResponse:
    """Kick off a full market run now (includes weekly-tier tickers).

    Returns immediately; a watchlist run takes minutes to hours of LLM time.
    Progress lands in logs, Telegram, and the email digest. The pipeline's
    internal lock serializes this with any scheduled run already in flight.
    """
    task = asyncio.create_task(run_market(market, include_weekly=True))
    _background_runs.add(task)
    task.add_done_callback(_background_runs.discard)
    logger.info("Manual run triggered for %s", market.value)
    return RunTriggeredResponse(market=market.value)
