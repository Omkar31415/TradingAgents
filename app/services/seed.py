"""First-run watchlist seed: a diversified starter set across the three markets.

Rationale (2026-07): liquid large caps with rich data coverage (news, social,
fundamentals all resolve well), spread across sectors so the assistant isn't
just an AI-trade tracker — AI infra (NVDA), mega-cap tech (MSFT, AMZN),
healthcare (LLY), financials (JPM); India core holdings across energy,
banking, IT, and telecom; and the two flagship crypto assets. The Phase 3
screener will grow/replace this automatically; until then, manage via the
watchlist API.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import ScheduleSlot
from app.repositories.schedule import ScheduleRepository
from app.repositories.watchlist import WatchlistRepository

logger = logging.getLogger(__name__)

SEED_SYMBOLS = [
    # US
    "NVDA", "MSFT", "AMZN", "LLY", "JPM",
    # India (NSE)
    "RELIANCE.NS", "HDFCBANK.NS", "INFY.NS", "BHARTIARTL.NS",
    # Crypto
    "BTC-USD", "ETH-USD",
]

# Default analysis windows (user-editable in the dashboard). Only two enabled
# out of the box: with an Ollama-cloud free tier of roughly 10-12 full runs
# per week, 2 runs/day is the sustainable ceiling. Times are in the user's
# local zone (America/Chicago) but each slot's timezone is editable.
SEED_SLOTS = [
    ScheduleSlot(label="US pre-market", run_time="07:30", timezone="America/Chicago",
                 market="us", enabled=True, max_tickers=1),
    ScheduleSlot(label="India pre-market", run_time="21:30", timezone="America/Chicago",
                 market="india", enabled=True, max_tickers=1),
    ScheduleSlot(label="US midday", run_time="12:00", timezone="America/Chicago",
                 market="us", enabled=False, max_tickers=1),
    ScheduleSlot(label="Crypto evening", run_time="18:00", timezone="America/Chicago",
                 market="crypto", enabled=False, max_tickers=1),
]


async def seed_watchlist_if_empty(session: AsyncSession) -> int:
    """Insert the starter watchlist when the table is empty. Returns rows added."""
    repo = WatchlistRepository(session)
    if await repo.count() > 0:
        return 0
    for symbol in SEED_SYMBOLS:
        await repo.add(symbol, added_by="seed")
    logger.info("Seeded watchlist with %d starter tickers", len(SEED_SYMBOLS))
    return len(SEED_SYMBOLS)


async def seed_schedule_if_empty(session: AsyncSession) -> int:
    """Insert the default schedule slots when the table is empty. Returns rows added."""
    repo = ScheduleRepository(session)
    if await repo.count() > 0:
        return 0
    for slot in SEED_SLOTS:
        await repo.add(slot)
    logger.info("Seeded %d default schedule slots", len(SEED_SLOTS))
    return len(SEED_SLOTS)


async def seed_paper_account_if_missing(session: AsyncSession, starting_cash: float) -> bool:
    """Create the paper account on first run. Returns True if created."""
    from app.repositories.portfolio import PortfolioRepository

    repo = PortfolioRepository(session)
    if await repo.get_account() is not None:
        return False
    await repo.create_account(starting_cash)
    logger.info("Created paper account with $%.2f virtual cash", starting_cash)
    return True
