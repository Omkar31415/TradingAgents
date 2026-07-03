"""Watchlist rotation rules — pure logic, no I/O.

Token-spend control: a ticker "wastes" LLM budget when it produces no
actionable signal run after run, not when its price falls (a falling price
with a timely Sell call is the assistant doing its job). So demotion is
driven by consecutive Hold ratings only, and any actionable rating promotes
a ticker straight back to daily coverage. Tickers are never auto-deleted:
history and the engine's reflection loop stay intact, and a demoted ticker
still gets a weekly check-in that can resurface it.
"""

from app.domain import HOLD_RATING, Tier


def next_rotation_state(
    tier: Tier,
    consecutive_holds: int,
    rating: str,
    *,
    demote_after: int,
) -> tuple[Tier, int]:
    """Return the (tier, consecutive_holds) a ticker should have after a run.

    Paused tickers are never rotated automatically — pausing is a manual
    decision and stays manual.
    """
    if tier is Tier.PAUSED:
        return tier, consecutive_holds

    if rating == HOLD_RATING:
        holds = consecutive_holds + 1
        if tier is Tier.DAILY and holds >= demote_after:
            return Tier.WEEKLY, holds
        return tier, holds

    # Any actionable rating (Buy/Overweight/Underweight/Sell): reset the
    # counter and make sure the ticker is back on daily coverage.
    return Tier.DAILY, 0
