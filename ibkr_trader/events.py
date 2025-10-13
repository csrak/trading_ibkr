"""Event bus and event definitions for the trading platform."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, DefaultDict, Generic, TypeVar

from ibkr_trader.models import OrderStatus, SymbolContract


class EventTopic(str, Enum):
    """Enumerates supported event channels."""

    ORDER_STATUS = "order_status"
    MARKET_DATA = "market_data"
    ACCOUNT = "account"


@dataclass(frozen=True, slots=True)
class OrderStatusEvent:
    """Payload emitted when an order status update is received."""

    order_id: int
    status: OrderStatus
    contract: SymbolContract
    filled: int
    remaining: int
    avg_fill_price: float
    timestamp: datetime


PayloadT = TypeVar("PayloadT")


class EventSubscription(Generic[PayloadT]):
    """Async iterator over events for a given topic."""

    def __init__(self, bus: "EventBus", topic: EventTopic) -> None:
        self._bus = bus
        self._topic = topic
        self._queue: asyncio.Queue[PayloadT] = asyncio.Queue()
        self._active = True
        self._bus._register(topic, self._queue)

    def __aiter__(self) -> AsyncIterator[PayloadT]:
        return self

    async def __anext__(self) -> PayloadT:
        if not self._active:
            raise StopAsyncIteration
        return await self._queue.get()

    async def get(self) -> PayloadT:
        """Retrieve the next event payload."""
        return await self.__anext__()

    def close(self) -> None:
        """Unsubscribe from the event bus."""
        if self._active:
            self._active = False
            self._bus._unregister(self._topic, self._queue)

    async def __aenter__(self) -> "EventSubscription[PayloadT]":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        self.close()


class EventBus:
    """Simple pub/sub event bus built on asyncio queues."""

    def __init__(self) -> None:
        self._topics: DefaultDict[EventTopic, list[asyncio.Queue[Any]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def subscribe(self, topic: EventTopic) -> EventSubscription[Any]:
        """Subscribe to a topic."""
        return EventSubscription(self, topic)

    async def publish(self, topic: EventTopic, payload: Any) -> None:
        """Publish payload to all subscribers of topic."""
        async with self._lock:
            queues = list(self._topics.get(topic, []))
        for queue in queues:
            await queue.put(payload)

    def _register(self, topic: EventTopic, queue: asyncio.Queue[Any]) -> None:
        self._topics[topic].append(queue)

    def _unregister(self, topic: EventTopic, queue: asyncio.Queue[Any]) -> None:
        subscribers = self._topics.get(topic)
        if not subscribers:
            return
        try:
            subscribers.remove(queue)
        except ValueError:
            return
        if not subscribers:
            self._topics.pop(topic, None)
