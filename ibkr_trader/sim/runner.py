"""Replay runner that orchestrates market data playback and strategy execution."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from ibkr_trader.events import EventBus, EventSubscription, EventTopic
from ibkr_trader.models import OrderSide
from ibkr_trader.sim.events import EventLoader, ReplayEvent
from ibkr_trader.sim.mock_broker import MockBroker
from model.data.models import OptionSurfaceEntry, OrderBookSnapshot, TradeEvent


class ReplayStrategy:
    """Minimal strategy interface for replay simulations."""

    async def on_order_book(
        self, snapshot: OrderBookSnapshot, broker: MockBroker
    ) -> None:  # pragma: no cover - override in subclass
        return None

    async def on_trade(
        self, trade: TradeEvent, broker: MockBroker
    ) -> None:  # pragma: no cover - override in subclass
        return None

    async def on_option_surface(
        self, entry: OptionSurfaceEntry, broker: MockBroker
    ) -> None:  # pragma: no cover - override in subclass
        return None

    async def on_fill(
        self, side: OrderSide, quantity: int
    ) -> None:  # pragma: no cover - override in subclass
        return None


class ReplayRunner:
    """Coordinates event replay and strategy execution."""

    def __init__(
        self,
        loader: EventLoader,
        strategy: ReplayStrategy,
        event_bus: EventBus | None = None,
    ) -> None:
        self.loader = loader
        self.strategy = strategy
        self.event_bus = event_bus or EventBus()
        self.broker = MockBroker(self.event_bus)

    async def run(self) -> None:
        async with self.event_bus.subscribe(EventTopic.EXECUTION) as exec_sub:
            execution_task = asyncio.create_task(self._handle_executions(exec_sub))
            try:
                for replay_event in self.loader.load_events():
                    await self._dispatch(replay_event)
            finally:
                await asyncio.sleep(0)
                execution_task.cancel()
                with suppress(asyncio.CancelledError):
                    await execution_task

    async def _dispatch(self, replay_event: ReplayEvent) -> None:
        payload = replay_event.payload
        if isinstance(payload, OrderBookSnapshot):
            await self.strategy.on_order_book(payload, self.broker)
        elif isinstance(payload, TradeEvent):
            await self.strategy.on_trade(payload, self.broker)
        elif isinstance(payload, OptionSurfaceEntry):
            await self.strategy.on_option_surface(payload, self.broker)

    async def _handle_executions(self, subscription: EventSubscription) -> None:
        while True:
            event = await subscription.get()
            if hasattr(event, "side") and hasattr(event, "quantity"):
                await self.strategy.on_fill(event.side, event.quantity)
