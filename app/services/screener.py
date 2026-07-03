"""The anomaly screener: cheap quantitative discovery of under-followed strength.

Runs daily (no LLM — pure data APIs, costs nothing against the run budget):

1. Candidates from Yahoo's predefined screens (US small-cap gainers, growth
   tech, undervalued growth, aggressive small caps) plus a custom India query.
2. Each candidate enriched with fundamentals (revenue/earnings growth,
   margins), momentum, legal insider-transaction filings (net buying), and
   StockTwits attention (best-effort).
3. Scored by ``screener_rules.anomaly_score``; the top scorers that clear
   MIN_SCORE_TO_ADD are auto-added to the watchlist (added_by="screener"),
   where the normal analysis slots pick them up stalest-first.

Crypto is intentionally out of scope: "hidden gem" small-cap coins are
overwhelmingly manipulation-driven; the watchlist's majors stay curated.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from app.core.config import get_settings
from app.domain import Market, infer_market
from app.models.base import session_factory
from app.models.entities import ScreenerResult
from app.repositories.screener import ScreenerRepository
from app.repositories.watchlist import WatchlistRepository
from app.services.notifier import Notifier
from app.services.screener_rules import (
    MIN_SCORE_TO_ADD,
    CandidateMetrics,
    anomaly_score,
    describe,
)

logger = logging.getLogger(__name__)

_US_SCREENS = (
    "small_cap_gainers",
    "growth_technology_stocks",
    "undervalued_growth_stocks",
    "aggressive_small_caps",
)
_PER_SCREEN = 25
_MAX_ENRICHED = 30  # cap enrichment API volume per run


def _collect_candidates_sync() -> list[str]:
    """Symbols from Yahoo screens, deduped, NSE preferred over BSE duplicates."""
    import yfinance as yf

    symbols: list[str] = []
    for screen in _US_SCREENS:
        try:
            for quote in yf.screen(screen, count=_PER_SCREEN).get("quotes", []):
                symbol = quote.get("symbol")
                if symbol:
                    symbols.append(symbol)
        except Exception as exc:
            logger.warning("Screen %r failed: %s", screen, exc)

    try:
        from yfinance import EquityQuery

        query = EquityQuery("and", [
            EquityQuery("eq", ["region", "in"]),
            # >50B INR market cap: liquid mid/small caps, not micro-cap traps
            EquityQuery("gt", ["intradaymarketcap", 50_000_000_000]),
        ])
        result = yf.screen(query, count=_PER_SCREEN, sortField="percentchange", sortAsc=False)
        for quote in result.get("quotes", []):
            symbol = quote.get("symbol", "")
            if symbol.endswith(".NS"):  # skip .BO duplicates of the same company
                symbols.append(symbol)
    except Exception as exc:
        logger.warning("India screen failed: %s", exc)

    seen: set[str] = set()
    unique = [s for s in symbols if not (s in seen or seen.add(s))]
    return unique


def _insider_net_shares_sync(ticker) -> float | None:
    """Net insider shares bought minus sold from recent Form-4 style filings."""
    try:
        frame = ticker.insider_transactions
        if frame is None or frame.empty:
            return None
        text_col = next(
            (c for c in ("Text", "Transaction", "transactionText") if c in frame.columns), None
        )
        shares_col = next((c for c in ("Shares", "shares") if c in frame.columns), None)
        if text_col is None or shares_col is None:
            return None
        net = 0.0
        for _, row in frame.head(40).iterrows():
            text = str(row.get(text_col, "")).lower()
            shares = row.get(shares_col)
            if shares is None:
                continue
            try:
                shares = float(shares)
            except (TypeError, ValueError):
                continue
            if "purchase" in text or "buy" in text:
                net += shares
            elif "sale" in text or "sold" in text:
                net -= shares
        return net
    except Exception:
        return None


def _watchers_sync(symbol: str) -> int | None:
    """StockTwits watchlist count — the attention meter. Best-effort."""
    import requests

    try:
        response = requests.get(
            f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=8,
        )
        if response.status_code != 200:
            return None
        return response.json().get("symbol", {}).get("watchlist_count")
    except Exception:
        return None


def _enrich_sync(symbol: str) -> CandidateMetrics:
    import yfinance as yf

    from tradingagents.dataflows.symbol_utils import normalize_symbol

    ticker = yf.Ticker(normalize_symbol(symbol))
    info: dict = {}
    try:
        info = ticker.info or {}
    except Exception:
        logger.warning("info fetch failed for %s", symbol)

    return_3m = None
    try:
        history = ticker.history(period="3mo")
        if len(history) >= 2:
            first, last = float(history["Close"].iloc[0]), float(history["Close"].iloc[-1])
            return_3m = (last - first) / first
    except Exception:
        pass

    return CandidateMetrics(
        symbol=symbol.upper(),
        market=infer_market(symbol).value,
        revenue_growth=info.get("revenueGrowth"),
        earnings_growth=info.get("earningsGrowth"),
        profit_margins=info.get("profitMargins"),
        return_3m=return_3m,
        week52_change=info.get("52WeekChange"),
        market_cap=info.get("marketCap"),
        watchers=_watchers_sync(symbol) if infer_market(symbol) is Market.US else None,
        insider_net_shares=_insider_net_shares_sync(ticker),
    )


async def run_screener() -> list[dict]:
    """One full screener pass. Returns scored results (dicts for the API/UI)."""
    settings = get_settings()
    run_date = datetime.now(timezone.utc).date().isoformat()

    candidates = await asyncio.to_thread(_collect_candidates_sync)
    async with session_factory()() as session:
        watchlist_repo = WatchlistRepository(session)
        existing = {t.symbol for t in await watchlist_repo.list_all()}
        watchlist_size = len(existing)
    fresh = [s for s in candidates if s.upper() not in existing][:_MAX_ENRICHED]
    logger.info(
        "Screener: %d candidates (%d new, enriching %d)",
        len(candidates), len(candidates) - (len(candidates) - len(fresh)), len(fresh),
    )

    scored: list[tuple[float, CandidateMetrics]] = []
    for symbol in fresh:
        metrics = await asyncio.to_thread(_enrich_sync, symbol)
        score = anomaly_score(metrics)
        if score is not None:
            scored.append((score, metrics))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    capacity = max(0, settings.screener_watchlist_cap - watchlist_size)
    budget = min(settings.screener_max_adds, capacity)
    notifier = Notifier(settings)
    results: list[dict] = []

    async with session_factory()() as session, session.begin():
        screener_repo = ScreenerRepository(session)
        watchlist_repo = WatchlistRepository(session)
        for rank, (score, metrics) in enumerate(scored):
            add = rank < budget and score >= MIN_SCORE_TO_ADD
            if add:
                await watchlist_repo.add(metrics.symbol, added_by="screener")
            await screener_repo.add(ScreenerResult(
                run_date=run_date,
                symbol=metrics.symbol,
                market=metrics.market,
                score=score,
                summary=describe(metrics),
                metrics_json=json.dumps(metrics.__dict__),
                added=add,
            ))
            results.append({
                "symbol": metrics.symbol, "market": metrics.market,
                "score": score, "summary": describe(metrics), "added": add,
            })

    for r in results:
        if r["added"]:
            await notifier.send_telegram(
                f"🔎 <b>Screener pick: {r['symbol']}</b> (score {r['score']:.0f})\n"
                f"{r['summary']}\n"
                f"Added to the watchlist — the next analysis slot will do the deep dive."
            )
    logger.info("Screener finished: %d scored, %d added", len(results),
                sum(1 for r in results if r["added"]))
    return results
