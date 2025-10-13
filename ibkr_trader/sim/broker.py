"""Simulated broker that mimics IBKR interactions for backtesting."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from ibkr_trader.constants import MARKET_DATA_IDLE_SLEEP_SECONDS
from ibkr_trader.events import (
    EventBus,
    EventTopic,
    ExecutionEvent,
    MarketDataEvent,
    OrderStatusEvent,
)
from ibkr_trader.models import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    Position,
    SymbolContract,
)

if TYPE_CHECKING:
    from ibkr_trader.portfolio import RiskGuard


class SimulatedBroker:
    """Minimal broker used for backtesting without a live IBKR connection."""

    def __init__(
        self,
        event_bus: EventBus,
        risk_guard: RiskGuard | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._risk_guard = risk_guard
        self._order_id = 1
        self._lock = asyncio.Lock()
        self.execution_events: list[ExecutionEvent] = []
        self._positions: dict[str, int] = {}

    async def place_order(self, request: OrderRequest) -> OrderResult:
        async with self._lock:
            order_id = self._order_id
            self._order_id += 1

        price_for_risk = (
            request.expected_price or request.limit_price or request.stop_price or Decimal("0")
        )

        if self._risk_guard is not None:
            await self._risk_guard.validate_order(
                contract=request.contract,
                side=request.side,
                quantity=request.quantity,
                price=price_for_risk,
            )

        await self._event_bus.publish(
            EventTopic.ORDER_STATUS,
            OrderStatusEvent(
                order_id=order_id,
                status=OrderStatus.SUBMITTED,
                contract=request.contract,
                side=request.side,
                filled=0,
                remaining=request.quantity,
                avg_fill_price=0.0,
                timestamp=datetime.now(UTC),
            ),
        )

        await asyncio.sleep(MARKET_DATA_IDLE_SLEEP_SECONDS)

        fill_price = price_for_risk

        execution_event = ExecutionEvent(
            order_id=order_id,
            contract=request.contract,
            side=request.side,
            quantity=request.quantity,
            price=fill_price,
            commission=Decimal("0"),
            timestamp=datetime.now(UTC),
        )
        self.execution_events.append(execution_event)
        await self._event_bus.publish(EventTopic.EXECUTION, execution_event)

        self._update_position(request.contract.symbol, request.side, request.quantity)

        return OrderResult(
            order_id=order_id,
            contract=request.contract,
            side=request.side,
            quantity=request.quantity,
            order_type=request.order_type,
            status=OrderStatus.FILLED,
            filled_quantity=request.quantity,
            avg_fill_price=fill_price,
        )

    async def get_positions(self) -> list[Position]:
        return [
            Position(
                contract=SymbolContract(symbol=symbol),
                quantity=quantity,
                avg_cost=Decimal("0"),
                market_value=Decimal("0"),
                unrealized_pnl=Decimal("0"),
            )
            for symbol, quantity in self._positions.items()
            if quantity != 0
        ]

    def _update_position(self, symbol: str, side: OrderSide, quantity: int) -> None:
        delta = quantity if side == OrderSide.BUY else -quantity
        self._positions[symbol] = self._positions.get(symbol, 0) + delta
        if self._positions[symbol] == 0:
            self._positions.pop(symbol, None)


class SimulatedMarketData:
    """Publishes historical prices onto the event bus during backtests."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus

    @asynccontextmanager
    async def stream(self, symbol: str) -> AsyncIterator[None]:
        yield

    async def publish_price(self, symbol: str, price: Decimal) -> None:
        await self._event_bus.publish(
            EventTopic.MARKET_DATA,
            MarketDataEvent(symbol=symbol, price=price, timestamp=datetime.now(UTC)),
        )
