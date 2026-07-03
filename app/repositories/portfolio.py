"""Repository for the paper account, positions (paper + real), and trades."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import PaperAccount, Position, Trade


class PortfolioRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    # --- account ---

    async def get_account(self) -> PaperAccount | None:
        result = await self._session.execute(select(PaperAccount).limit(1))
        return result.scalar_one_or_none()

    async def create_account(self, starting_cash: float) -> PaperAccount:
        account = PaperAccount(starting_cash=starting_cash, cash=starting_cash)
        self._session.add(account)
        await self._session.flush()
        return account

    # --- positions ---

    async def list_positions(self, account_type: str | None = None) -> list[Position]:
        stmt = select(Position).order_by(Position.symbol)
        if account_type:
            stmt = stmt.where(Position.account_type == account_type)
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def get_position(self, account_type: str, symbol: str) -> Position | None:
        result = await self._session.execute(
            select(Position).where(
                Position.account_type == account_type,
                Position.symbol == symbol.upper(),
            )
        )
        return result.scalar_one_or_none()

    async def get_position_by_id(self, position_id: int) -> Position | None:
        return await self._session.get(Position, position_id)

    async def add_position(self, position: Position) -> Position:
        self._session.add(position)
        await self._session.flush()
        return position

    async def remove_position(self, position: Position) -> None:
        await self._session.delete(position)

    # --- trades ---

    async def add_trade(self, trade: Trade) -> Trade:
        self._session.add(trade)
        await self._session.flush()
        return trade

    async def list_trades(self, limit: int = 100) -> list[Trade]:
        result = await self._session.execute(
            select(Trade).order_by(Trade.executed_at.desc()).limit(limit)
        )
        return list(result.scalars())
