"""Tests for the internal event bus."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ibkr_trader.events import EventBus, EventTopic, OrderStatusEvent
from ibkr_trader.models import OrderSide, OrderStatus, SymbolContract


@pytest.mark.asyncio
async def test_event_bus_publish_and_receive() -> None:
    bus = EventBus()
    subscription = bus.subscribe(EventTopic.ORDER_STATUS)

    event = OrderStatusEvent(
        order_id=1,
        status=OrderStatus.SUBMITTED,
        contract=SymbolContract(symbol="AAPL"),
        side=OrderSide.BUY,
        filled=0,
        remaining=1,
        avg_fill_price=0.0,
        timestamp=datetime.now(UTC),
    )

    await bus.publish(EventTopic.ORDER_STATUS, event)

    received = await asyncio.wait_for(subscription.get(), timeout=0.1)
    assert received == event
    subscription.close()


@pytest.mark.asyncio
async def test_multiple_subscribers_receive_same_event() -> None:
    bus = EventBus()
    sub_a = bus.subscribe(EventTopic.ORDER_STATUS)
    sub_b = bus.subscribe(EventTopic.ORDER_STATUS)

    event = OrderStatusEvent(
        order_id=5,
        status=OrderStatus.SUBMITTED,
        contract=SymbolContract(symbol="QQQ"),
        side=OrderSide.BUY,
        filled=0,
        remaining=1,
        avg_fill_price=0.0,
        timestamp=datetime.now(UTC),
    )

    await bus.publish(EventTopic.ORDER_STATUS, event)

    received_a = await asyncio.wait_for(sub_a.get(), timeout=0.1)
    received_b = await asyncio.wait_for(sub_b.get(), timeout=0.1)

    assert received_a == event
    assert received_b == event

    sub_a.close()
    sub_b.close()
