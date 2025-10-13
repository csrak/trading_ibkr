"""Market data subscription management for IBKR and external feeds."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from loguru import logger

from ibkr_trader.events import EventBus, EventTopic, MarketDataEvent
from ibkr_trader.models import SymbolContract


@dataclass(frozen=True, slots=True)
class SubscriptionRequest:
    """Represents a market data subscription request."""

    contract: SymbolContract
    snapshot: bool = False


class MarketDataService:
    """Manage market data subscriptions and publish updates to the event bus."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._lock = asyncio.Lock()
        self._active_requests: dict[str, int] = defaultdict(int)

    async def publish_price(self, symbol: str, price: Decimal) -> None:
        """Publish a price update to the bus (external feed integration point)."""
        event = MarketDataEvent(symbol=symbol, price=price, timestamp=datetime.now(UTC))
        await self._event_bus.publish(EventTopic.MARKET_DATA, event)

    @asynccontextmanager
    async def subscribe(self, request: SubscriptionRequest) -> AsyncIterator[None]:
        """Context manager to track subscription lifecycle.

        This stub currently just guards against exceeding local limits. Integration with
        IBKR streaming requests can be added later.
        """
        key = request.contract.symbol
        async with self._lock:
            self._active_requests[key] += 1
            if self._active_requests[key] > 50:
                logger.warning(
                    "Market data subscription count for %s exceeded local soft limit", key
                )
        try:
            yield
        finally:
            async with self._lock:
                self._active_requests[key] -= 1
                if self._active_requests[key] <= 0:
                    self._active_requests.pop(key, None)
