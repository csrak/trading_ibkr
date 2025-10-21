"""Adapters to bridge replay strategies to live execution contexts.

This module enables strategies designed for market replay (with on_order_book, on_trade callbacks)
to work in live trading mode by adapting them to use the event bus and on_bar callbacks.
"""

from __future__ import annotations

from decimal import Decimal

from ibkr_trader.base_strategy import BaseStrategy, BrokerProtocol
from ibkr_trader.events import EventBus
from ibkr_trader.strategy import Strategy, StrategyConfig


class ConfigBasedLiveStrategy(Strategy):
    """Wrapper that adapts config-based replay strategies to live execution.

    This adapter allows advanced strategies (mean reversion, vol overlay, etc.) that were
    designed for replay mode to work in live trading by:
    1. Receiving market data from the event bus (on_bar callbacks)
    2. Forwarding to the underlying strategy's on_bar implementation
    3. Maintaining compatibility with the Strategy base class for live execution

    Note: Currently only supports strategies that use on_bar() callbacks.
    Strategies requiring on_order_book() or other microstructure data will need
    additional adapter work (Phase 1 Step 5).
    """

    def __init__(
        self,
        impl: BaseStrategy,
        broker: BrokerProtocol,
        event_bus: EventBus,
        symbol: str,
    ) -> None:
        """Initialize the live strategy adapter.

        Args:
            impl: The underlying strategy implementation (extends BaseStrategy)
            broker: Broker instance for order execution
            event_bus: Event bus for market data and execution events
            symbol: Trading symbol
        """
        # Create a minimal config for the Strategy base class
        config = StrategyConfig(name=type(impl).__name__, symbols=[symbol])
        super().__init__(config=config, broker=broker, event_bus=event_bus)
        self.impl = impl
        self._symbol = symbol

    async def on_bar(
        self, symbol: str, price: Decimal, broker: BrokerProtocol, **kwargs: object
    ) -> None:
        """Forward price bar updates to the underlying strategy.

        Args:
            symbol: Trading symbol
            price: Current price
            broker: Broker instance
            **kwargs: Optional OHLC data (high, low, volume)
        """
        await self.impl.on_bar(symbol, price, broker, **kwargs)
