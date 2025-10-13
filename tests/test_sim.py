"""Tests for the simulated broker and market data."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from ibkr_trader.events import EventBus, EventTopic
from ibkr_trader.models import OrderRequest, OrderSide, OrderStatus, OrderType, SymbolContract
from ibkr_trader.sim.broker import SimulatedBroker


@pytest.mark.asyncio
async def test_simulated_broker_places_order() -> None:
    bus = EventBus()
    broker = SimulatedBroker(event_bus=bus)
    status_sub = bus.subscribe(EventTopic.ORDER_STATUS)
    exec_sub = bus.subscribe(EventTopic.EXECUTION)

    request = OrderRequest(
        contract=SymbolContract(symbol="AAPL"),
        side=OrderSide.BUY,
        quantity=2,
        order_type=OrderType.MARKET,
        expected_price=Decimal("150"),
    )

    result = await broker.place_order(request)

    status = await asyncio.wait_for(status_sub.get(), timeout=0.1)
    execution = await asyncio.wait_for(exec_sub.get(), timeout=0.1)

    assert result.status == OrderStatus.FILLED
    assert status.order_id == execution.order_id
    assert execution.quantity == 2
    assert execution.price == Decimal("150")
