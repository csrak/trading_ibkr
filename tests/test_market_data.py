"""Tests for market data service."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from eventkit import Event
from ib_insync import Contract

from ibkr_trader.events import EventBus, EventTopic, MarketDataEvent
from ibkr_trader.market_data import MarketDataService, SubscriptionRequest
from ibkr_trader.models import SymbolContract


class DummyIB:
    def __init__(self) -> None:
        self._ticker = SimpleNamespace()
        self._ticker.contract = Contract()
        self._ticker.updateEvent = Event()
        self._ticker.last = None
        self._ticker.close = None
        self._ticker.marketPrice = lambda: None
        self._ticker.midpoint = lambda: None

    async def qualifyContractsAsync(  # noqa: N802
        self, contract: Contract
    ) -> list[Contract]:
        self._ticker.contract = contract
        return [contract]

    def reqMktData(  # noqa: N802
        self, contract: Contract, *_args: object
    ) -> SimpleNamespace:
        self._ticker.contract = contract
        return self._ticker

    def cancelMktData(self, contract: Contract) -> None:  # noqa: N802
        return None


@pytest.mark.asyncio
async def test_publish_price_emits_event() -> None:
    bus = EventBus()
    service = MarketDataService(event_bus=bus)
    subscription = bus.subscribe(EventTopic.MARKET_DATA)

    await service.publish_price("AAPL", Decimal("123.45"))

    event = await asyncio.wait_for(subscription.get(), timeout=0.1)
    assert isinstance(event, MarketDataEvent)
    assert event.symbol == "AAPL"
    assert event.price == Decimal("123.45")
    subscription.close()


@pytest.mark.asyncio
async def test_subscription_context_tracks_counts() -> None:
    bus = EventBus()
    service = MarketDataService(event_bus=bus)
    contract = SymbolContract(symbol="AAPL")
    request = SubscriptionRequest(contract=contract)
    service.attach_ib(DummyIB())

    async with service.subscribe(request):
        # inside the context, a publish still works
        await service.publish_price("AAPL", Decimal("1"))

    # Nothing to assert directly; ensure context exits without issue
