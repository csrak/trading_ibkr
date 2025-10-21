"""Unified base strategy interface for live and simulation trading.

This module defines the foundational abstractions that allow strategies to work
seamlessly across live trading (IBKRBroker), backtesting (SimulatedBroker), and
market replay (MockBroker) contexts.
"""

from __future__ import annotations

from abc import ABC
from decimal import Decimal
from typing import Protocol, runtime_checkable

from ibkr_trader.models import OrderRequest, OrderResult, OrderSide, Position
from model.data.models import OptionSurfaceEntry, OrderBookSnapshot, TradeEvent


@runtime_checkable
class BrokerProtocol(Protocol):
    """Protocol that all broker implementations must satisfy.

    This allows strategies to work with IBKRBroker, SimulatedBroker, or MockBroker
    without tight coupling to any specific implementation.
    """

    async def place_order(self, request: OrderRequest) -> OrderResult:
        """Place an order and return the result."""
        ...

    async def get_positions(self) -> list[Position]:
        """Get current positions across all symbols."""
        ...


class BaseStrategy(ABC):  # noqa: B024
    """Base class for all trading strategies (live and simulation).

    Strategies implement callback methods for different data types and execution contexts.
    All callbacks are optional - strategies only implement what they need.

    For simple price-based strategies:
        - Implement on_bar() for tick-by-tick price updates

    For advanced microstructure strategies:
        - Implement on_order_book() for L2 order book data
        - Implement on_trade() for individual trade events
        - Implement on_option_surface() for options market making

    For execution tracking:
        - Implement on_fill() to track fills across all contexts
    """

    # Price bar callbacks (most common)
    async def on_bar(  # noqa: B027
        self, symbol: str, price: Decimal, broker: BrokerProtocol, **kwargs: object
    ) -> None:
        """Process a new price bar / tick update.

        This is the primary callback for strategies based on price movement.
        Called for each market data event in live trading or backtest replay.

        Args:
            symbol: Trading symbol
            price: Current price (close price for bar data)
            broker: Broker instance for order submission and position queries
            **kwargs: Optional OHLC data (high, low, volume) from MarketDataEvent

        Kwargs:
            high (Decimal): Bar high price (defaults to price if not provided)
            low (Decimal): Bar low price (defaults to price if not provided)
            volume (int): Bar volume (optional)

        Example:
            async def on_bar(
                self, symbol: str, price: Decimal, broker: BrokerProtocol, **kwargs
            ) -> None:
                position = await self.get_position(symbol, broker)
                high = kwargs.get("high", price)  # Get high or use price as default
                low = kwargs.get("low", price)    # Get low or use price as default
                if self.should_buy(price) and position <= 0:
                    await broker.place_order(...)
        """
        pass

    # Advanced market microstructure callbacks (optional)
    async def on_order_book(  # noqa: B027
        self, snapshot: OrderBookSnapshot, broker: BrokerProtocol
    ) -> None:
        """Process an order book (L2) snapshot.

        For strategies that need full market depth information.
        Primarily used in replay simulations with historical L2 data.

        Args:
            snapshot: Order book snapshot with bid/ask levels
            broker: Broker instance for order submission
        """
        pass

    async def on_trade(self, trade: TradeEvent, broker: BrokerProtocol) -> None:  # noqa: B027
        """Process an individual trade event.

        For strategies analyzing trade flow or microstructure signals.

        Args:
            trade: Trade event with price, size, side
            broker: Broker instance for order submission
        """
        pass

    async def on_option_surface(  # noqa: B027
        self, entry: OptionSurfaceEntry, broker: BrokerProtocol
    ) -> None:
        """Process an option surface update.

        For options-based strategies (vol arb, skew trading, market making).

        Args:
            entry: Option surface entry with strike, expiry, IV, greeks
            broker: Broker instance for order submission
        """
        pass

    # Execution callback (all contexts)
    async def on_fill(self, side: OrderSide, quantity: int) -> None:  # noqa: B027
        """Process an order fill event.

        Called after any order is filled, regardless of broker type.
        Useful for maintaining internal position tracking or risk metrics.

        Args:
            side: Order side (BUY/SELL) that was filled
            quantity: Filled quantity
        """
        pass

    # Lifecycle methods (implemented by framework)
    async def start(self) -> None:  # noqa: B027
        """Initialize strategy and begin processing events.

        Override if strategy needs startup logic (loading models, etc.)
        """
        pass

    async def stop(self) -> None:  # noqa: B027
        """Stop strategy and cleanup resources.

        Override if strategy needs cleanup logic.
        """
        pass

    # Utility methods available to all strategies
    async def get_position(self, symbol: str, broker: BrokerProtocol) -> int:
        """Get current position for a symbol.

        Args:
            symbol: Trading symbol
            broker: Broker instance to query

        Returns:
            Position quantity (positive=long, negative=short, 0=flat)
        """
        positions = await broker.get_positions()
        for pos in positions:
            if pos.contract.symbol == symbol:
                return pos.quantity
        return 0
