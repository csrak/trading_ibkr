"""Tests for strategy event integration."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ibkr_trader.events import EventBus, EventTopic, MarketDataEvent
from ibkr_trader.models import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    Position,
    SymbolContract,
)
from ibkr_trader.strategy import SimpleMovingAverageStrategy, SMAConfig


class StubBroker:
    """Minimal broker stub to record orders and positions."""

    def __init__(self) -> None:
        self.orders: list[OrderRequest] = []
        self.position_map: dict[str, int] = {}

    async def get_positions(self) -> list[Position]:
        positions: list[Position] = []
        for symbol, quantity in self.position_map.items():
            positions.append(
                Position(
                    contract=SymbolContract(symbol=symbol),
                    quantity=quantity,
                    avg_cost=Decimal("0"),
                    market_value=Decimal("0"),
                    unrealized_pnl=Decimal("0"),
                )
            )
        return positions

    async def place_order(self, order_request: OrderRequest) -> OrderResult:
        self.orders.append(order_request)
        return OrderResult(
            order_id=len(self.orders),
            contract=order_request.contract,
            side=order_request.side,
            quantity=order_request.quantity,
            order_type=order_request.order_type,
            status=OrderStatus.SUBMITTED,
            filled_quantity=0,
            avg_fill_price=Decimal("0"),
        )


async def _emit_prices(event_bus: EventBus, symbol: str, prices: list[float | Decimal]) -> None:
    for price in prices:
        event = MarketDataEvent(
            symbol=symbol,
            price=Decimal(str(price)),
            timestamp=datetime.now(UTC),
        )
        await event_bus.publish(EventTopic.MARKET_DATA, event)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_sma_strategy_generates_buy_signal() -> None:
    event_bus = EventBus()
    broker = StubBroker()
    config = SMAConfig(symbols=["AAPL"], fast_period=2, slow_period=3, position_size=1)
    strategy = SimpleMovingAverageStrategy(config=config, broker=broker, event_bus=event_bus)

    await strategy.start()
    try:
        await _emit_prices(event_bus, "AAPL", [3, 2, 1, 2, 3])
        await asyncio.sleep(0.1)
    finally:
        await strategy.stop()

    assert broker.orders, "Expected buy order to be submitted"
    order = broker.orders[0]
    assert order.side == OrderSide.BUY
    assert order.quantity == 1


@pytest.mark.asyncio
async def test_sma_strategy_generates_sell_signal() -> None:
    event_bus = EventBus()
    broker = StubBroker()
    broker.position_map["AAPL"] = 1
    config = SMAConfig(symbols=["AAPL"], fast_period=2, slow_period=3, position_size=1)
    strategy = SimpleMovingAverageStrategy(config=config, broker=broker, event_bus=event_bus)

    await strategy.start()
    try:
        await _emit_prices(event_bus, "AAPL", [1, 2, 3, 2, 1])
        await asyncio.sleep(0.1)
    finally:
        await strategy.stop()

    assert broker.orders, "Expected sell order to be submitted"
    order = broker.orders[-1]
    assert order.side == OrderSide.SELL
