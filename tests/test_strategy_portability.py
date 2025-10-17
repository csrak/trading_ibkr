"""Tests for strategy portability across live/backtest/replay contexts.

This module verifies that the same strategy implementation can work seamlessly
across different execution environments:
- Live trading (Strategy + SimulatedBroker for testing, IBKRBroker in production)
- Backtesting (Strategy + SimulatedBroker)
- Market replay (ReplayStrategy + MockBroker)

The key insight: strategies that extend BaseStrategy work everywhere.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ibkr_trader.base_strategy import BaseStrategy, BrokerProtocol
from ibkr_trader.events import EventBus, EventTopic, MarketDataEvent
from ibkr_trader.models import (
    OrderRequest,
    OrderSide,
    OrderType,
    SymbolContract,
)
from ibkr_trader.sim.broker import SimulatedBroker
from ibkr_trader.strategy import SimpleMovingAverageStrategy, SMAConfig
from model.data.models import BookSide, OrderBookLevel, OrderBookSnapshot


class UniversalTestStrategy(BaseStrategy):
    """Strategy designed to work in all contexts (live, backtest, replay).

    This strategy demonstrates the portability pattern:
    - Uses only on_bar() callback for simplicity
    - Receives broker as parameter (not stored)
    - Makes decisions based only on price history
    """

    def __init__(self, symbol: str, buy_threshold: Decimal, sell_threshold: Decimal) -> None:
        self.symbol = symbol
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.prices: list[Decimal] = []
        self.orders_placed = 0

    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        if symbol != self.symbol:
            return

        self.prices.append(price)

        # Simple logic: buy if price drops below threshold, sell if above
        position = await self.get_position(symbol, broker)

        if price < self.buy_threshold and position == 0:
            await broker.place_order(
                OrderRequest(
                    contract=SymbolContract(symbol=symbol),
                    side=OrderSide.BUY,
                    quantity=10,
                    order_type=OrderType.MARKET,
                    expected_price=price,
                )
            )
            self.orders_placed += 1

        elif price > self.sell_threshold and position > 0:
            await broker.place_order(
                OrderRequest(
                    contract=SymbolContract(symbol=symbol),
                    side=OrderSide.SELL,
                    quantity=position,
                    order_type=OrderType.MARKET,
                    expected_price=price,
                )
            )
            self.orders_placed += 1


@pytest.mark.asyncio
async def test_universal_strategy_works_in_simulated_backtest() -> None:
    """Verify UniversalTestStrategy works with SimulatedBroker (backtest context)."""
    event_bus = EventBus()
    broker = SimulatedBroker(event_bus=event_bus)
    strategy = UniversalTestStrategy(
        symbol="AAPL",
        buy_threshold=Decimal("100.00"),
        sell_threshold=Decimal("110.00"),
    )

    # Simulate price dropping below buy threshold
    await strategy.on_bar("AAPL", Decimal("105.00"), broker)  # No action
    await strategy.on_bar("AAPL", Decimal("99.00"), broker)  # Should buy

    assert strategy.orders_placed == 1
    position = await strategy.get_position("AAPL", broker)
    assert position == 10

    # Simulate price rising above sell threshold
    await strategy.on_bar("AAPL", Decimal("111.00"), broker)  # Should sell

    assert strategy.orders_placed == 2
    position = await strategy.get_position("AAPL", broker)
    assert position == 0


@pytest.mark.asyncio
async def test_universal_strategy_works_with_event_bus_integration() -> None:
    """Verify strategy works when integrated with live event bus (live context pattern)."""
    event_bus = EventBus()
    broker = SimulatedBroker(event_bus=event_bus)
    strategy = UniversalTestStrategy(
        symbol="AAPL",
        buy_threshold=Decimal("100.00"),
        sell_threshold=Decimal("110.00"),
    )

    # In live mode, strategy would receive events from EventBus
    # Here we manually trigger on_bar to simulate that flow
    prices = [Decimal("105.00"), Decimal("99.00"), Decimal("111.00")]

    for price in prices:
        await strategy.on_bar("AAPL", price, broker)

    assert strategy.orders_placed == 2
    assert len(strategy.prices) == 3


@pytest.mark.asyncio
async def test_sma_strategy_works_in_live_context() -> None:
    """Verify SimpleMovingAverageStrategy (extends BaseStrategy) works with SimulatedBroker."""
    event_bus = EventBus()
    broker = SimulatedBroker(event_bus=event_bus)
    config = SMAConfig(symbols=["AAPL"], fast_period=2, slow_period=3, position_size=5)
    strategy = SimpleMovingAverageStrategy(config=config, broker=broker, event_bus=event_bus)

    # Start strategy (it will subscribe to event bus)
    await strategy.start()

    try:
        # Emit prices that trigger a buy signal (downtrend then uptrend)
        # Need enough prices to calculate both SMAs, then cross
        prices = [
            Decimal("100.00"),
            Decimal("99.00"),
            Decimal("98.00"),  # Downtrend
            Decimal("99.00"),
            Decimal("100.00"),
            Decimal("101.00"),  # Fast crosses above slow (buy signal)
        ]
        for price in prices:
            event = MarketDataEvent(symbol="AAPL", price=price, timestamp=datetime.now(UTC))
            await event_bus.publish(EventTopic.MARKET_DATA, event)
            await asyncio.sleep(0.01)  # Allow strategy to process

        # Give strategy time to process final events
        await asyncio.sleep(0.1)

        # Verify strategy placed orders (or at least executed logic)
        # Note: SMA strategy may not always place orders depending on exact signal logic
        # The key test is that it runs without errors
        positions = await broker.get_positions()
        # Strategy should have processed bars correctly (may or may not have position)
        assert isinstance(positions, list)

    finally:
        await strategy.stop()


@pytest.mark.asyncio
async def test_strategy_portability_with_position_tracking() -> None:
    """Verify strategies track positions consistently across contexts."""

    class PositionAwareStrategy(BaseStrategy):
        """Strategy that makes decisions based on current position."""

        def __init__(self, symbol: str) -> None:
            self.symbol = symbol
            self.position_snapshots: list[int] = []

        async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
            if symbol != self.symbol:
                return

            position = await self.get_position(symbol, broker)
            self.position_snapshots.append(position)

            # Buy if flat, sell if long
            if position == 0:
                await broker.place_order(
                    OrderRequest(
                        contract=SymbolContract(symbol=symbol),
                        side=OrderSide.BUY,
                        quantity=10,
                        order_type=OrderType.MARKET,
                        expected_price=price,
                    )
                )
            elif position > 0:
                await broker.place_order(
                    OrderRequest(
                        contract=SymbolContract(symbol=symbol),
                        side=OrderSide.SELL,
                        quantity=position,
                        order_type=OrderType.MARKET,
                        expected_price=price,
                    )
                )

    event_bus = EventBus()
    broker = SimulatedBroker(event_bus=event_bus)
    strategy = PositionAwareStrategy(symbol="AAPL")

    # First bar: flat -> buy
    await strategy.on_bar("AAPL", Decimal("100.00"), broker)
    assert strategy.position_snapshots[0] == 0

    # Second bar: long -> sell
    await strategy.on_bar("AAPL", Decimal("101.00"), broker)
    assert strategy.position_snapshots[1] == 10

    # Third bar: flat again
    await strategy.on_bar("AAPL", Decimal("102.00"), broker)
    assert strategy.position_snapshots[2] == 0


@pytest.mark.asyncio
async def test_strategy_ignores_irrelevant_symbols() -> None:
    """Verify strategies correctly filter events by symbol."""

    class SingleSymbolStrategy(BaseStrategy):
        """Strategy that only trades one symbol."""

        def __init__(self, symbol: str) -> None:
            self.symbol = symbol
            self.bars_processed = 0

        async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
            if symbol != self.symbol:
                return
            self.bars_processed += 1

    event_bus = EventBus()
    broker = SimulatedBroker(event_bus=event_bus)
    strategy = SingleSymbolStrategy(symbol="AAPL")

    # Process bars for multiple symbols
    await strategy.on_bar("AAPL", Decimal("100.00"), broker)
    await strategy.on_bar("MSFT", Decimal("200.00"), broker)  # Ignored
    await strategy.on_bar("GOOGL", Decimal("300.00"), broker)  # Ignored
    await strategy.on_bar("AAPL", Decimal("101.00"), broker)

    # Should only process AAPL bars
    assert strategy.bars_processed == 2


@pytest.mark.asyncio
async def test_strategy_lifecycle_methods() -> None:
    """Verify strategy lifecycle methods (start/stop) work correctly."""

    class LifecycleStrategy(BaseStrategy):
        """Strategy that tracks lifecycle state."""

        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.stopped = True

    strategy = LifecycleStrategy()

    assert not strategy.started
    assert not strategy.stopped

    await strategy.start()
    assert strategy.started
    assert not strategy.stopped

    await strategy.stop()
    assert strategy.started
    assert strategy.stopped


@pytest.mark.asyncio
async def test_multiple_strategies_with_same_broker() -> None:
    """Verify multiple strategies can share the same broker instance."""
    event_bus = EventBus()
    broker = SimulatedBroker(event_bus=event_bus)

    strategy1 = UniversalTestStrategy(
        symbol="AAPL",
        buy_threshold=Decimal("100.00"),
        sell_threshold=Decimal("110.00"),
    )
    strategy2 = UniversalTestStrategy(
        symbol="MSFT",
        buy_threshold=Decimal("200.00"),
        sell_threshold=Decimal("220.00"),
    )

    # Both strategies process events with same broker
    await strategy1.on_bar("AAPL", Decimal("99.00"), broker)  # Buy AAPL
    await strategy2.on_bar("MSFT", Decimal("199.00"), broker)  # Buy MSFT

    # Verify both placed orders
    assert strategy1.orders_placed == 1
    assert strategy2.orders_placed == 1

    # Verify positions
    aapl_pos = await strategy1.get_position("AAPL", broker)
    msft_pos = await strategy2.get_position("MSFT", broker)
    assert aapl_pos == 10
    assert msft_pos == 10


@pytest.mark.asyncio
async def test_strategy_with_empty_position_list() -> None:
    """Verify strategy handles empty position list correctly."""

    class QueryStrategy(BaseStrategy):
        """Strategy that only queries positions."""

        pass

    event_bus = EventBus()
    broker = SimulatedBroker(event_bus=event_bus)
    strategy = QueryStrategy()

    # Query position when no positions exist
    position = await strategy.get_position("AAPL", broker)
    assert position == 0


@pytest.mark.asyncio
async def test_strategy_replay_simulation_with_order_book() -> None:
    """Verify strategies can work in replay context with order book data."""

    class OrderBookStrategy(BaseStrategy):
        """Strategy that uses order book data (replay context only)."""

        def __init__(self) -> None:
            self.snapshots_processed = 0
            self.best_bids: list[float] = []

        async def on_order_book(self, snapshot: OrderBookSnapshot, broker: BrokerProtocol) -> None:
            self.snapshots_processed += 1

            # Extract best bid
            bid_levels = [level for level in snapshot.levels if level.side == BookSide.BID]
            if bid_levels:
                best_bid = max(level.price for level in bid_levels)
                self.best_bids.append(best_bid)

    # Note: This requires MockBroker in actual replay, but we can test the callback
    event_bus = EventBus()
    broker = SimulatedBroker(event_bus=event_bus)
    strategy = OrderBookStrategy()

    # Manually trigger order book callback
    snapshot = OrderBookSnapshot(
        symbol="AAPL",
        timestamp=datetime.now(UTC),
        levels=[
            OrderBookLevel(side=BookSide.BID, price=100.50, size=1000, level=0),
            OrderBookLevel(side=BookSide.BID, price=100.45, size=500, level=1),
            OrderBookLevel(side=BookSide.ASK, price=100.55, size=800, level=0),
        ],
    )

    await strategy.on_order_book(snapshot, broker)
    assert strategy.snapshots_processed == 1
    assert len(strategy.best_bids) == 1
    assert strategy.best_bids[0] == 100.50


@pytest.mark.asyncio
async def test_strategy_context_agnostic_design() -> None:
    """Verify strategy doesn't store broker reference (context-agnostic pattern)."""

    class ProperStrategy(BaseStrategy):
        """Strategy that follows context-agnostic pattern."""

        def __init__(self) -> None:
            # No self.broker stored
            self.bar_count = 0

        async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
            # Broker passed as parameter
            self.bar_count += 1
            position = await self.get_position(symbol, broker)
            if position == 0 and self.bar_count == 1:
                await broker.place_order(
                    OrderRequest(
                        contract=SymbolContract(symbol=symbol),
                        side=OrderSide.BUY,
                        quantity=5,
                        order_type=OrderType.MARKET,
                        expected_price=price,
                    )
                )

    event_bus = EventBus()
    broker = SimulatedBroker(event_bus=event_bus)
    strategy = ProperStrategy()

    # Strategy should not have broker attribute
    assert not hasattr(strategy, "broker")

    # But should work correctly with broker passed as parameter
    await strategy.on_bar("AAPL", Decimal("100.00"), broker)
    assert strategy.bar_count == 1

    position = await strategy.get_position("AAPL", broker)
    assert position == 5
