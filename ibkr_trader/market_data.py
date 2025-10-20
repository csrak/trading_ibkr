"""Market data subscription management for IBKR and external feeds."""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from ib_insync import IB, Contract, Ticker
from loguru import logger

from ibkr_trader.constants import SUBSCRIPTION_SOFT_LIMIT
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
        self._ib: IB | None = None
        self._subscriptions: dict[str, tuple[Ticker, Callable[[Ticker], None]]] = {}

    async def publish_price(
        self, symbol: str, price: Decimal, timestamp: datetime | None = None
    ) -> None:
        """Publish a price update to the bus (external feed integration point)."""
        event_time = timestamp or datetime.now(UTC)
        event = MarketDataEvent(symbol=symbol, price=price, timestamp=event_time)
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
            if self._active_requests[key] > SUBSCRIPTION_SOFT_LIMIT:
                logger.warning(
                    "Market data subscription count for {} exceeded local soft limit {}",
                    key,
                    SUBSCRIPTION_SOFT_LIMIT,
                )
        try:
            await self._start_stream(request)
            yield
        finally:
            await self._stop_stream(key)
            async with self._lock:
                self._active_requests[key] -= 1
                if self._active_requests[key] <= 0:
                    self._active_requests.pop(key, None)

    def attach_ib(self, ib: IB) -> None:
        """Attach an active IB instance for live market data streaming."""
        self._ib = ib

    async def _start_stream(self, request: SubscriptionRequest) -> None:
        if self._ib is None:
            raise RuntimeError("IBKR connection not attached to MarketDataService")

        symbol = request.contract.symbol
        if symbol in self._subscriptions:
            return

        contract = Contract()
        contract.symbol = request.contract.symbol
        contract.secType = request.contract.sec_type
        contract.exchange = request.contract.exchange
        contract.currency = request.contract.currency

        qualified = await self._ib.qualifyContractsAsync(contract)
        if not qualified:
            raise ValueError(f"Unable to qualify contract for market data: {symbol}")
        contract = qualified[0]

        ticker = self._ib.reqMktData(contract, "", request.snapshot, request.snapshot)
        loop = asyncio.get_running_loop()

        def _on_update(_: Ticker) -> None:
            price = ticker.last or ticker.close or ticker.marketPrice() or ticker.midpoint()
            if price is None or math.isnan(price):
                return
            loop.create_task(self.publish_price(symbol, Decimal(str(price))))

        ticker.updateEvent += _on_update
        self._subscriptions[symbol] = (ticker, _on_update)

    async def _stop_stream(self, symbol: str) -> None:
        if self._ib is None:
            return
        info = self._subscriptions.pop(symbol, None)
        if info is None:
            return
        ticker, callback = info
        ticker.updateEvent -= callback
        self._ib.cancelMktData(ticker.contract)
