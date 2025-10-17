"""Order book (L2 market depth) streaming service for IBKR.

This module provides Level 2 market depth data for advanced trading strategies
that need order book information beyond simple price quotes.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ib_insync import IB, Contract, Ticker
from loguru import logger

from ibkr_trader.events import EventBus, EventTopic
from ibkr_trader.models import SymbolContract
from model.data.models import BookSide, OrderBookLevel, OrderBookSnapshot


class OrderBookEvent:
    """Event published when order book updates.

    This event wraps an OrderBookSnapshot and is published to the event bus
    whenever the order book changes.
    """

    def __init__(self, snapshot: OrderBookSnapshot) -> None:
        self.snapshot = snapshot
        self.timestamp = snapshot.timestamp


class OrderBookService:
    """Manage L2 market depth subscriptions from IBKR.

    This service:
    - Subscribes to IBKR market depth (L2) data
    - Converts IBKR DOMLevel format to our OrderBookSnapshot model
    - Publishes OrderBookEvent to the event bus for strategies to consume

    Note: IBKR market depth has limitations:
    - Most stocks: 5 levels max
    - Futures: 10 levels max
    - Requires market data subscriptions (additional fees may apply)
    """

    def __init__(self, event_bus: EventBus, num_levels: int = 5) -> None:
        """Initialize order book service.

        Args:
            event_bus: Event bus for publishing order book updates
            num_levels: Number of price levels to request (default 5, max varies by instrument)
        """
        self._event_bus = event_bus
        self._num_levels = num_levels
        self._ib: IB | None = None
        self._subscriptions: dict[str, Ticker] = {}

    def attach_ib(self, ib: IB) -> None:
        """Attach an active IBKR connection.

        Args:
            ib: Connected ib_insync IB instance
        """
        self._ib = ib
        logger.debug("OrderBookService attached to IB connection")

    async def subscribe(self, contract: SymbolContract, symbol: str) -> None:
        """Subscribe to order book updates for a symbol.

        Args:
            contract: Symbol contract specification
            symbol: Symbol identifier for tracking

        Raises:
            RuntimeError: If IB connection not attached
            ValueError: If contract cannot be qualified
        """
        if self._ib is None:
            raise RuntimeError("IB connection not attached to OrderBookService")

        if symbol in self._subscriptions:
            logger.warning(f"Already subscribed to order book for {symbol}")
            return

        # Create and qualify IB contract
        ib_contract = Contract()
        ib_contract.symbol = contract.symbol
        ib_contract.secType = contract.sec_type
        ib_contract.exchange = contract.exchange
        ib_contract.currency = contract.currency

        qualified = await self._ib.qualifyContractsAsync(ib_contract)
        if not qualified:
            raise ValueError(f"Unable to qualify contract for order book: {symbol}")

        ib_contract = qualified[0]

        # Request market depth
        ticker = self._ib.reqMktDepth(ib_contract, numRows=self._num_levels)

        # Register update callbacks
        loop = asyncio.get_running_loop()

        def _on_update(_: Ticker) -> None:
            """Callback fired when order book updates."""
            loop.create_task(self._publish_snapshot(symbol, ticker))

        ticker.domBidsUpdateEvent += _on_update
        ticker.domAsksUpdateEvent += _on_update

        self._subscriptions[symbol] = ticker
        logger.info(f"Subscribed to L2 order book: {symbol} ({self._num_levels} levels)")

    async def unsubscribe(self, symbol: str) -> None:
        """Unsubscribe from order book updates.

        Args:
            symbol: Symbol to unsubscribe from
        """
        ticker = self._subscriptions.pop(symbol, None)
        if ticker and self._ib:
            self._ib.cancelMktDepth(ticker.contract)
            logger.info(f"Unsubscribed from order book: {symbol}")

    async def _publish_snapshot(self, symbol: str, ticker: Ticker) -> None:
        """Convert IBKR DOMLevel data to OrderBookSnapshot and publish to event bus.

        Args:
            symbol: Symbol identifier
            ticker: IBKR ticker object with order book data
        """
        levels: list[OrderBookLevel] = []

        # Convert bid levels
        for level_idx, dom_level in enumerate(ticker.domBids):
            if dom_level.price > 0:  # Valid price level
                levels.append(
                    OrderBookLevel(
                        side=BookSide.BID,
                        price=dom_level.price,
                        size=dom_level.size,
                        level=level_idx,
                        num_orders=None,  # IBKR doesn't provide order count in standard depth
                    )
                )

        # Convert ask levels
        for level_idx, dom_level in enumerate(ticker.domAsks):
            if dom_level.price > 0:  # Valid price level
                levels.append(
                    OrderBookLevel(
                        side=BookSide.ASK,
                        price=dom_level.price,
                        size=dom_level.size,
                        level=level_idx,
                        num_orders=None,
                    )
                )

        if not levels:
            # No valid data yet, skip publishing
            return

        snapshot = OrderBookSnapshot(
            timestamp=datetime.now(UTC),
            symbol=symbol,
            levels=levels,
            venue="IBKR",
        )

        event = OrderBookEvent(snapshot)
        await self._event_bus.publish(EventTopic.ORDER_BOOK, event)
        logger.debug(f"Order book snapshot for {symbol}: {len(levels)} levels")
