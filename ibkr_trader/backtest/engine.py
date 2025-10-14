"""Core backtesting engine that reuses the live event-driven runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal

from loguru import logger

from ibkr_trader.constants import MARKET_DATA_IDLE_SLEEP_SECONDS
from ibkr_trader.events import EventBus, EventTopic, ExecutionEvent, OrderStatusEvent
from ibkr_trader.portfolio import PortfolioState, RiskGuard
from ibkr_trader.sim.broker import SimulatedBroker, SimulatedMarketData
from ibkr_trader.strategy import Strategy


class BacktestEngine:
    """Runs strategies against historical bar data."""

    def __init__(
        self,
        symbol: str,
        event_bus: EventBus,
        market_data: SimulatedMarketData,
        broker: SimulatedBroker,
        portfolio: PortfolioState,
        risk_guard: RiskGuard,
    ) -> None:
        self.symbol = symbol
        self.event_bus = event_bus
        self.market_data = market_data
        self.broker = broker
        self.portfolio = portfolio
        self.risk_guard = risk_guard

    async def run(
        self,
        strategy: Strategy,
        bars: Iterable[tuple[datetime | str, Decimal | float]],
    ) -> None:
        order_task = asyncio.create_task(self._order_listener())
        execution_task = asyncio.create_task(self._execution_listener())

        await strategy.start()

        async def _publish(price_time: datetime, price_value: Decimal) -> None:
            await self.market_data.publish_price(self.symbol, price_value, timestamp=price_time)
            await asyncio.sleep(0)

        for price_time, price_value in _normalize_bars(bars):
            await _publish(price_time, price_value)

        await asyncio.sleep(MARKET_DATA_IDLE_SLEEP_SECONDS)

        await strategy.stop()

        order_task.cancel()
        execution_task.cancel()
        with suppress(asyncio.CancelledError):
            await order_task
        with suppress(asyncio.CancelledError):
            await execution_task

        logger.info("Backtest finished. Executions=%s", len(self.broker.execution_events))

    async def _order_listener(self) -> None:
        subscription = self.event_bus.subscribe(EventTopic.ORDER_STATUS)
        try:
            async for event in subscription:
                if isinstance(event, OrderStatusEvent):
                    await self.risk_guard.handle_order_status(event)
                    await self.portfolio.persist()
        except asyncio.CancelledError:
            raise

    async def _execution_listener(self) -> None:
        subscription = self.event_bus.subscribe(EventTopic.EXECUTION)
        try:
            async for event in subscription:
                if isinstance(event, ExecutionEvent):
                    await self.portfolio.record_execution_event(event)
                    await self.portfolio.persist()
        except asyncio.CancelledError:
            raise


def _normalize_bars(
    bars: Iterable[tuple[datetime | str, Decimal | float]],
) -> Iterable[tuple[datetime, Decimal]]:
    for timestamp, price in bars:
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        else:
            timestamp = timestamp.astimezone(UTC)
        if not isinstance(price, Decimal):
            price = Decimal(str(price))
        yield timestamp, price
