"""Orchestrates analysis runs: slot-based scheduled windows and manual triggers.

A run analyzes a batch of watchlist tickers sequentially, persists each
outcome, applies watchlist rotation, alerts on rating changes, and sends one
digest email per batch.

DB sessions are short-lived: one to snapshot the batch, then one per ticker to
persist its outcome. A batch takes minutes-to-hours of LLM time, and holding a
single transaction across it would just invite lock trouble for zero benefit.

Quota guard: ``assistant_daily_run_budget`` caps ticker-runs per UTC day
across all slots and manual triggers, so a misconfigured schedule cannot burn
a limited LLM quota (e.g. Ollama cloud free tier) in one day.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

import pytz

from app.core.config import get_settings
from app.core.logging import run_id_var
from app.domain import Market, Tier
from app.models.base import session_factory
from app.models.entities import SignalRecord
from app.repositories.portfolio import PortfolioRepository
from app.repositories.schedule import ScheduleRepository
from app.repositories.signals import SignalRepository
from app.repositories.watchlist import WatchlistRepository
from app.services.notifier import Notifier
from app.services.paper_broker import execute_signal, live_price, usd_rate
from app.services.rotation import next_rotation_state
from app.services.runner import run_analysis_sync

logger = logging.getLogger(__name__)

MARKET_TZ = {
    Market.US: pytz.timezone("America/New_York"),
    Market.INDIA: pytz.timezone("Asia/Kolkata"),
    Market.CRYPTO: pytz.utc,
}

# Serializes batches so a manual trigger can't overlap a scheduled one
# (duplicate spend, provider rate limits). Scheduler jobs also set
# max_instances=1.
_run_lock = asyncio.Lock()


@dataclass(frozen=True)
class TickerItem:
    symbol: str
    asset_type: str
    prev_rating: str | None
    market: Market


def _utc_midnight() -> datetime:
    now = datetime.now(pytz.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def runs_remaining_today() -> int:
    """How many ticker-runs the daily budget still allows."""
    async with session_factory()() as session:
        used = await SignalRepository(session).count_since(_utc_midnight())
    return max(0, get_settings().assistant_daily_run_budget - used)


async def run_slot(slot_id: int) -> list[dict]:
    """Execute one scheduled slot: the stalest due tickers up to its budget."""
    async with session_factory()() as session:
        slot = await ScheduleRepository(session).get(slot_id)
    if slot is None or not slot.enabled:
        logger.info("Slot %s missing or disabled; skipping", slot_id)
        return []

    remaining = await runs_remaining_today()
    if remaining <= 0:
        logger.warning("Daily run budget exhausted; skipping slot %r", slot.label)
        return []
    batch_size = min(slot.max_tickers, remaining)

    market = Market(slot.market) if slot.market else None
    tz = pytz.timezone(slot.timezone)
    include_weekly = datetime.now(tz).weekday() == 0

    async with session_factory()() as session:
        due = await WatchlistRepository(session).get_due_for_run(
            market, include_weekly, limit=batch_size
        )
        items = [
            TickerItem(t.symbol, t.asset_type, t.last_rating, Market(t.market)) for t in due
        ]
    return await _run_batch(slot.label, items)


async def run_market(market: Market, include_weekly: bool = True) -> list[dict]:
    """Manual full-market run (dashboard / API trigger). Budget still applies."""
    remaining = await runs_remaining_today()
    if remaining <= 0:
        logger.warning("Daily run budget exhausted; refusing manual %s run", market.value)
        return []
    async with session_factory()() as session:
        due = await WatchlistRepository(session).get_due_for_run(
            market, include_weekly, limit=remaining
        )
        items = [
            TickerItem(t.symbol, t.asset_type, t.last_rating, Market(t.market)) for t in due
        ]
    return await _run_batch(f"manual {market.value}", items)


async def _run_batch(label: str, items: list[TickerItem]) -> list[dict]:
    settings = get_settings()
    notifier = Notifier(settings)

    async with _run_lock:
        run_id_var.set(f"{label.replace(' ', '-')}-{uuid4().hex[:8]}")
        logger.info("Batch started: %r, %d ticker(s)", label, len(items))

        digest_rows: list[dict] = []
        batch_date = ""
        for item in items:
            trade_date = datetime.now(MARKET_TZ[item.market]).date().isoformat()
            batch_date = batch_date or trade_date
            outcome = await asyncio.to_thread(
                run_analysis_sync, item.symbol, trade_date, item.asset_type, settings
            )
            row = await _persist_outcome(item.market, item.symbol, item.prev_rating, outcome)
            digest_rows.append(row)

            if not outcome.ok:
                await notifier.alert_run_error(item.symbol, trade_date, outcome.error or "")
            elif row["changed"]:
                await notifier.alert_rating_change(
                    symbol=item.symbol,
                    market=item.market,
                    trade_date=trade_date,
                    prev_rating=item.prev_rating,
                    rating=outcome.rating or "?",
                    decision_text=outcome.decision_text,
                    holding_note=await _real_holding_context(item.symbol),
                )

            # Mirror the signal into the paper book (mechanical, live prices).
            if outcome.ok and outcome.rating:
                try:
                    summary = await execute_signal(
                        item.symbol, item.market, outcome.rating, outcome.decision_text
                    )
                except Exception:
                    logger.exception("Paper execution failed for %s", item.symbol)
                    summary = None
                if summary:
                    await notifier.send_telegram(f"📄 <b>Paper trade</b>: {summary}")

        if digest_rows:
            await notifier.send_digest(label, batch_date, digest_rows)
        logger.info("Batch finished: %r", label)
        return digest_rows


async def _real_holding_context(symbol: str) -> str | None:
    """P&L context for the user's real position in this symbol, if any."""
    async with session_factory()() as session:
        position = await PortfolioRepository(session).get_position("real", symbol)
    if position is None:
        return None
    price = await live_price(symbol)
    rate = await usd_rate(position.currency)
    since = position.opened_at.date().isoformat() if position.opened_at else "?"
    qty = f"{position.quantity:.4f}".rstrip("0").rstrip(".")
    if price is None or rate is None:
        return f"You hold {qty} since {since} @ {position.avg_price:,.2f}"
    pct = (price - position.avg_price) / position.avg_price * 100
    return (
        f"You hold {qty} @ {position.avg_price:,.2f} since {since} "
        f"({pct:+.1f}% at today's {price:,.2f})"
    )


async def _persist_outcome(market, symbol, prev_rating, outcome) -> dict:
    """Store the signal record and apply watchlist rotation in one short transaction."""
    changed = outcome.ok and outcome.rating != prev_rating
    async with session_factory()() as session, session.begin():
        signals = SignalRepository(session)
        await signals.add(SignalRecord(
            symbol=symbol,
            market=market.value,
            trade_date=outcome.trade_date,
            rating=outcome.rating,
            prev_rating=prev_rating,
            changed=changed,
            status="success" if outcome.ok else "error",
            error=outcome.error,
            report_path=outcome.report_path,
            duration_seconds=outcome.duration_seconds,
        ))

        watchlist = WatchlistRepository(session)
        ticker = await watchlist.get_by_symbol(symbol)
        if ticker is not None:
            ticker.last_run_at = datetime.now(pytz.utc)
            if outcome.ok and outcome.rating:
                new_tier, holds = next_rotation_state(
                    Tier(ticker.tier),
                    ticker.consecutive_holds,
                    outcome.rating,
                    demote_after=get_settings().assistant_demote_after_holds,
                )
                if new_tier.value != ticker.tier:
                    logger.info(
                        "Rotation: %s %s -> %s after rating %s",
                        symbol, ticker.tier, new_tier.value, outcome.rating,
                    )
                ticker.tier = new_tier.value
                ticker.consecutive_holds = holds
                ticker.last_rating = outcome.rating

    return {
        "symbol": symbol,
        "prev_rating": prev_rating,
        "rating": outcome.rating,
        "changed": changed,
        "status": "success" if outcome.ok else "error",
        "error": outcome.error,
        "report_path": outcome.report_path,
    }
