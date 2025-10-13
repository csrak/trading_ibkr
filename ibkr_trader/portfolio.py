"""Portfolio state tracking and risk checks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from loguru import logger

from ibkr_trader.models import OrderSide, OrderStatus, Position, SymbolContract


@dataclass(slots=True)
class PortfolioSnapshot:
    """In-memory snapshot of positions and account metrics."""

    positions: dict[str, Position] = field(default_factory=dict)
    net_liquidation: Decimal = Decimal("0")
    total_cash: Decimal = Decimal("0")
    buying_power: Decimal = Decimal("0")
    realized_pnl_today: Decimal = Decimal("0")


class PortfolioState:
    """Tracks portfolio state and evaluates risk limits."""

    def __init__(self, max_daily_loss: Decimal) -> None:
        self.snapshot = PortfolioSnapshot()
        self._max_daily_loss = max_daily_loss
        self._loss_check_date: date | None = None
        self._lock = asyncio.Lock()

    async def update_account(self, summary: dict[str, str]) -> None:
        async with self._lock:
            self.snapshot.net_liquidation = Decimal(summary.get("NetLiquidation", "0"))
            self.snapshot.total_cash = Decimal(summary.get("TotalCashValue", "0"))
            self.snapshot.buying_power = Decimal(summary.get("BuyingPower", "0"))

    async def update_positions(self, positions: list[Position]) -> None:
        async with self._lock:
            self.snapshot.positions = {pos.contract.symbol: pos for pos in positions}

    async def record_order_fill(
        self, symbol: str, side: OrderSide, filled: int, avg_price: Decimal
    ) -> None:
        # placeholder for realized pnl tracking; to be expanded with executions
        logger.debug(
            "Order fill recorded: symbol=%s side=%s filled=%s avg_price=%s",
            symbol,
            side,
            filled,
            avg_price,
        )

    async def check_daily_loss_limit(self) -> None:
        async with self._lock:
            today = datetime.now(UTC).date()
            if self._loss_check_date != today:
                self._loss_check_date = today
                self.snapshot.realized_pnl_today = Decimal("0")

            if self.snapshot.realized_pnl_today <= -self._max_daily_loss:
                message = (
                    "Daily loss limit reached: "
                    f"{self.snapshot.realized_pnl_today} <= -{self._max_daily_loss}"
                )
                raise RuntimeError(message)


class RiskGuard:
    """Additional risk validations for advanced strategies."""

    def __init__(self, portfolio: PortfolioState, max_exposure: Decimal) -> None:
        self.portfolio = portfolio
        self.max_exposure = max_exposure

    async def validate_order(
        self, contract: SymbolContract, side: OrderSide, quantity: int, price: Decimal
    ) -> None:
        await self.portfolio.check_daily_loss_limit()

        exposure = quantity * price
        if exposure > self.max_exposure:
            message = (
                f"Order exposure {exposure} exceeds max exposure "
                f"{self.max_exposure} for {contract.symbol}"
            )
            raise RuntimeError(message)

    async def handle_order_status(
        self, symbol: str, status: OrderStatus, filled: int, avg_fill_price: Decimal
    ) -> None:
        if status == OrderStatus.FILLED and filled > 0:
            await self.portfolio.record_order_fill(symbol, OrderSide.BUY, filled, avg_fill_price)
