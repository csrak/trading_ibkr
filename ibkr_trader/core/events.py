"""Event bus and event definitions for the trading platform."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from ibkr_trader.models import OrderSide, OrderStatus, SymbolContract


class EventTopic(str, Enum):
    """Enumerates supported event channels."""

    ORDER_STATUS = "order_status"
    MARKET_DATA = "market_data"
    ACCOUNT = "account"
    EXECUTION = "execution"
    DIAGNOSTIC = "diagnostic"
    ORDER_BOOK = "order_book"  # L2 market depth updates
    ALERT = "alert"


@dataclass(frozen=True, slots=True)
class OrderStatusEvent:
    """Payload emitted when an order status update is received."""

    order_id: int
    status: OrderStatus
    contract: SymbolContract
    side: OrderSide
    filled: int
    remaining: int
    avg_fill_price: float
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class MarketDataEvent:
    """Payload representing a market data update for a symbol.

    Can represent either a tick (price only) or a bar (OHLC).
    When high/low are not provided, they default to price for backward compatibility.
    """

    symbol: str
    price: Decimal
    timestamp: datetime
    high: Decimal | None = None  # Bar high, defaults to price if None
    low: Decimal | None = None  # Bar low, defaults to price if None
    volume: int | None = None  # Bar volume, optional


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    """Payload representing an execution fill."""

    order_id: int
    contract: SymbolContract
    side: OrderSide
    quantity: int
    price: Decimal
    commission: Decimal
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    """Telemetry message for instrumentation warnings/info."""

    level: str
    message: str
    timestamp: datetime
    context: dict[str, object] | None = None


class EventSubscription:
    """Async iterator over events for a given topic."""

    def __init__(self, bus: EventBus, topic: EventTopic) -> None:
        self._bus = bus
        self._topic = topic
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._active = True
        self._bus._register(topic, self._queue)

    def __aiter__(self) -> AsyncIterator[object]:
        return self

    async def __anext__(self) -> object:
        if not self._active:
            raise StopAsyncIteration
        return await self._queue.get()

    async def get(self) -> object:
        """Retrieve the next event payload."""
        return await self.__anext__()

    def close(self) -> None:
        """Unsubscribe from the event bus."""
        if self._active:
            self._active = False
            self._bus._unregister(self._topic, self._queue)

    async def __aenter__(self) -> EventSubscription:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.close()


class EventBus:
    """Simple pub/sub event bus built on asyncio queues."""

    def __init__(self) -> None:
        self._topics: defaultdict[EventTopic, list[asyncio.Queue[object]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def subscribe(self, topic: EventTopic) -> EventSubscription:
        """Subscribe to a topic."""
        return EventSubscription(self, topic)

    async def publish(self, topic: EventTopic, payload: object) -> None:
        """Publish payload to all subscribers of topic."""
        async with self._lock:
            queues = list(self._topics.get(topic, []))
        for queue in queues:
            await queue.put(payload)

    def _register(self, topic: EventTopic, queue: asyncio.Queue[object]) -> None:
        self._topics[topic].append(queue)

    def _unregister(self, topic: EventTopic, queue: asyncio.Queue[object]) -> None:
        subscribers = self._topics.get(topic)
        if not subscribers:
            return
        try:
            subscribers.remove(queue)
        except ValueError:
            return
        if not subscribers:
            self._topics.pop(topic, None)
