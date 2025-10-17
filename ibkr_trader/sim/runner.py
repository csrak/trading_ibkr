"""Replay runner that orchestrates market data playback and strategy execution."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from ibkr_trader.base_strategy import BaseStrategy
from ibkr_trader.events import EventBus, EventSubscription, EventTopic
from ibkr_trader.sim.events import EventLoader, ReplayEvent
from ibkr_trader.sim.mock_broker import MockBroker
from model.data.models import OptionSurfaceEntry, OrderBookSnapshot, TradeEvent


class ReplayStrategy(BaseStrategy):
    """Strategy interface for replay simulations.

    Extends BaseStrategy to work with MockBroker in replay contexts.
    Strategies can now be used in BOTH live/backtest AND replay simulations.
    """

    pass  # All methods inherited from BaseStrategy with default implementations


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
        """Dispatch replay events to strategy callbacks.

        The broker is passed to callbacks to enable order submission.
        """
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
