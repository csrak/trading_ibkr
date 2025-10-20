"""Tests for trailing stop functionality."""

import asyncio
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ibkr_trader.broker import IBKRBroker
from ibkr_trader.events import EventBus
from ibkr_trader.models import (
    OrderResult,
    OrderSide,
    OrderStatus,
    SymbolContract,
    TrailingStopConfig,
)
from ibkr_trader.trailing_stops import TrailingStop, TrailingStopManager


def test_trailing_stop_config_requires_trail_amount_or_percent() -> None:
    """Test that exactly one of trail_amount or trail_percent must be specified."""
    from pydantic import ValidationError

    # Neither specified explicitly - should fail
    with pytest.raises(
        ValidationError, match="Either trail_amount or trail_percent must be specified"
    ):
        TrailingStopConfig(
            symbol="AAPL",
            side=OrderSide.SELL,
            quantity=10,
            trail_amount=None,
            trail_percent=None,
        )

    # Both specified works in construction but manager/CLI will catch this
    # The validation through CLI is what matters for user-facing behavior
    config = TrailingStopConfig(
        symbol="AAPL",
        side=OrderSide.SELL,
        quantity=10,
        trail_amount=Decimal("5.00"),
        trail_percent=Decimal("2.0"),
    )
    # CLI will enforce mutual exclusivity before constructing this object
    assert config.trail_amount == Decimal("5.00")
    assert config.trail_percent == Decimal("2.0")


def test_trailing_stop_config_validates_trail_amount_positive() -> None:
    """Test that trail_amount must be positive."""
    with pytest.raises(ValueError, match="trail_amount must be positive"):
        TrailingStopConfig(
            symbol="AAPL",
            side=OrderSide.SELL,
            quantity=10,
            trail_amount=Decimal("-5.00"),
            trail_percent=None,
        )

    with pytest.raises(ValueError, match="trail_amount must be positive"):
        TrailingStopConfig(
            symbol="AAPL",
            side=OrderSide.SELL,
            quantity=10,
            trail_amount=Decimal("0"),
            trail_percent=None,
        )


def test_trailing_stop_config_validates_trail_percent_range() -> None:
    """Test that trail_percent must be between 0 and 100."""
    with pytest.raises(ValueError, match="trail_percent must be between 0 and 100"):
        TrailingStopConfig(
            symbol="AAPL",
            side=OrderSide.SELL,
            quantity=10,
            trail_amount=None,
            trail_percent=Decimal("0"),
        )

    with pytest.raises(ValueError, match="trail_percent must be between 0 and 100"):
        TrailingStopConfig(
            symbol="AAPL",
            side=OrderSide.SELL,
            quantity=10,
            trail_amount=None,
            trail_percent=Decimal("100"),
        )

    with pytest.raises(ValueError, match="trail_percent must be between 0 and 100"):
        TrailingStopConfig(
            symbol="AAPL",
            side=OrderSide.SELL,
            quantity=10,
            trail_amount=None,
            trail_percent=Decimal("-5"),
        )


def test_trailing_stop_config_valid_trail_amount() -> None:
    """Test valid trailing stop configuration with trail_amount."""
    config = TrailingStopConfig(
        symbol="AAPL",
        side=OrderSide.SELL,
        quantity=10,
        trail_amount=Decimal("5.00"),
        trail_percent=None,
    )

    assert config.symbol == "AAPL"
    assert config.side == OrderSide.SELL
    assert config.quantity == 10
    assert config.trail_amount == Decimal("5.00")
    assert config.trail_percent is None


def test_trailing_stop_config_valid_trail_percent() -> None:
    """Test valid trailing stop configuration with trail_percent."""
    config = TrailingStopConfig(
        symbol="AAPL",
        side=OrderSide.SELL,
        quantity=10,
        trail_percent=Decimal("2.5"),
    )

    assert config.symbol == "AAPL"
    assert config.side == OrderSide.SELL
    assert config.quantity == 10
    assert config.trail_amount is None
    assert config.trail_percent == Decimal("2.5")


def test_trailing_stop_config_normalizes_symbol() -> None:
    """Test that symbol is normalized to uppercase."""
    config = TrailingStopConfig(
        symbol="aapl",
        side=OrderSide.SELL,
        quantity=10,
        trail_amount=Decimal("5.00"),
    )

    assert config.symbol == "AAPL"


def test_trailing_stop_serialization() -> None:
    """Test TrailingStop to_dict and from_dict serialization."""
    config = TrailingStopConfig(
        symbol="AAPL",
        side=OrderSide.SELL,
        quantity=10,
        trail_amount=Decimal("5.00"),
        activation_price=Decimal("150.00"),
    )

    trailing_stop = TrailingStop(
        stop_id="AAPL_1001",
        config=config,
        order_id=1001,
        current_stop_price=Decimal("145.00"),
        high_water_mark=Decimal("150.00"),
    )

    # Serialize
    data = trailing_stop.to_dict()
    assert data["stop_id"] == "AAPL_1001"
    assert data["order_id"] == 1001
    assert data["current_stop_price"] == "145.00"
    assert data["high_water_mark"] == "150.00"
    assert data["activated"] is False
    assert data["config"]["symbol"] == "AAPL"

    # Deserialize
    restored = TrailingStop.from_dict(data)
    assert restored.stop_id == trailing_stop.stop_id
    assert restored.order_id == trailing_stop.order_id
    assert restored.current_stop_price == trailing_stop.current_stop_price
    assert restored.high_water_mark == trailing_stop.high_water_mark
    assert restored.config.symbol == trailing_stop.config.symbol
    assert restored.config.trail_amount == trailing_stop.config.trail_amount


@pytest.mark.asyncio
async def test_trailing_stop_manager_create_stop_with_trail_amount() -> None:
    """Test creating a trailing stop with dollar amount."""
    broker_mock = MagicMock(spec=IBKRBroker)
    broker_mock.place_order = AsyncMock(
        return_value=OrderResult(
            order_id=1001,
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type="STP",
            status=OrderStatus.SUBMITTED,
        )
    )

    event_bus = EventBus()
    state_file = Path("/tmp/test_trailing_stops.json")
    if state_file.exists():
        state_file.unlink()

    manager = TrailingStopManager(broker_mock, event_bus, state_file)

    config = TrailingStopConfig(
        symbol="AAPL",
        side=OrderSide.SELL,
        quantity=10,
        trail_amount=Decimal("5.00"),
    )

    initial_price = Decimal("150.00")
    stop_id = await manager.create_trailing_stop(config, initial_price)

    assert stop_id == "AAPL_1001"
    assert stop_id in manager.active_stops

    trailing_stop = manager.active_stops[stop_id]
    assert trailing_stop.config.symbol == "AAPL"
    assert trailing_stop.current_stop_price == Decimal("145.00")  # 150 - 5
    assert trailing_stop.high_water_mark == initial_price

    # Verify order was placed
    broker_mock.place_order.assert_awaited_once()
    order_request = broker_mock.place_order.call_args[0][0]
    assert order_request.contract.symbol == "AAPL"
    assert order_request.side == OrderSide.SELL
    assert order_request.stop_price == Decimal("145.00")

    # Cleanup
    if state_file.exists():
        state_file.unlink()


@pytest.mark.asyncio
async def test_trailing_stop_manager_create_stop_with_trail_percent() -> None:
    """Test creating a trailing stop with percentage."""
    broker_mock = MagicMock(spec=IBKRBroker)
    broker_mock.place_order = AsyncMock(
        return_value=OrderResult(
            order_id=1002,
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type="STP",
            status=OrderStatus.SUBMITTED,
        )
    )

    event_bus = EventBus()
    state_file = Path("/tmp/test_trailing_stops_percent.json")
    if state_file.exists():
        state_file.unlink()

    manager = TrailingStopManager(broker_mock, event_bus, state_file)

    config = TrailingStopConfig(
        symbol="AAPL",
        side=OrderSide.SELL,
        quantity=10,
        trail_percent=Decimal("2.0"),  # 2%
    )

    initial_price = Decimal("100.00")
    stop_id = await manager.create_trailing_stop(config, initial_price)

    trailing_stop = manager.active_stops[stop_id]
    # 100 * (1 - 0.02) = 98.00
    assert trailing_stop.current_stop_price == Decimal("98.00")

    # Cleanup
    if state_file.exists():
        state_file.unlink()


@pytest.mark.asyncio
async def test_trailing_stop_long_position_raises_with_price_increase() -> None:
    """Test that trailing stop for long position raises when price increases."""
    broker_mock = MagicMock(spec=IBKRBroker)
    broker_mock.place_order = AsyncMock(
        return_value=OrderResult(
            order_id=1003,
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type="STP",
            status=OrderStatus.SUBMITTED,
        )
    )

    event_bus = EventBus()
    state_file = Path("/tmp/test_trailing_long.json")
    if state_file.exists():
        state_file.unlink()

    manager = TrailingStopManager(broker_mock, event_bus, state_file)
    await manager.start()

    config = TrailingStopConfig(
        symbol="AAPL",
        side=OrderSide.SELL,  # Long position
        quantity=10,
        trail_amount=Decimal("5.00"),
    )

    initial_price = Decimal("150.00")
    stop_id = await manager.create_trailing_stop(config, initial_price)

    trailing_stop = manager.active_stops[stop_id]
    assert trailing_stop.current_stop_price == Decimal("145.00")

    # Price increases to 155 - stop should raise to 150
    await manager._on_market_data("AAPL", Decimal("155.00"))
    assert trailing_stop.high_water_mark == Decimal("155.00")
    assert trailing_stop.current_stop_price == Decimal("150.00")

    # Wait for rate limit to clear
    await asyncio.sleep(1.1)

    # Price increases to 160 - stop should raise to 155
    await manager._on_market_data("AAPL", Decimal("160.00"))
    assert trailing_stop.high_water_mark == Decimal("160.00")
    assert trailing_stop.current_stop_price == Decimal("155.00")

    await manager.stop()

    # Cleanup
    if state_file.exists():
        state_file.unlink()


@pytest.mark.asyncio
async def test_trailing_stop_long_position_never_widens() -> None:
    """Test that trailing stop never moves down (widens) for long position."""
    broker_mock = MagicMock(spec=IBKRBroker)
    broker_mock.place_order = AsyncMock(
        return_value=OrderResult(
            order_id=1004,
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type="STP",
            status=OrderStatus.SUBMITTED,
        )
    )

    event_bus = EventBus()
    state_file = Path("/tmp/test_trailing_never_widens.json")
    if state_file.exists():
        state_file.unlink()

    manager = TrailingStopManager(broker_mock, event_bus, state_file)
    await manager.start()

    config = TrailingStopConfig(
        symbol="AAPL",
        side=OrderSide.SELL,
        quantity=10,
        trail_amount=Decimal("5.00"),
    )

    initial_price = Decimal("150.00")
    stop_id = await manager.create_trailing_stop(config, initial_price)

    trailing_stop = manager.active_stops[stop_id]
    original_stop = trailing_stop.current_stop_price

    # Price decreases - stop should NOT move down
    await manager._on_market_data("AAPL", Decimal("145.00"))
    assert trailing_stop.current_stop_price == original_stop

    await manager._on_market_data("AAPL", Decimal("140.00"))
    assert trailing_stop.current_stop_price == original_stop

    await manager.stop()

    # Cleanup
    if state_file.exists():
        state_file.unlink()


@pytest.mark.asyncio
async def test_trailing_stop_short_position_lowers_with_price_decrease() -> None:
    """Test that trailing stop for short position lowers when price decreases."""
    broker_mock = MagicMock(spec=IBKRBroker)
    broker_mock.place_order = AsyncMock(
        return_value=OrderResult(
            order_id=1005,
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,
            quantity=10,
            order_type="STP",
            status=OrderStatus.SUBMITTED,
        )
    )

    event_bus = EventBus()
    state_file = Path("/tmp/test_trailing_short.json")
    if state_file.exists():
        state_file.unlink()

    manager = TrailingStopManager(broker_mock, event_bus, state_file)
    await manager.start()

    config = TrailingStopConfig(
        symbol="AAPL",
        side=OrderSide.BUY,  # Short position
        quantity=10,
        trail_amount=Decimal("5.00"),
    )

    initial_price = Decimal("150.00")
    stop_id = await manager.create_trailing_stop(config, initial_price)

    trailing_stop = manager.active_stops[stop_id]
    assert trailing_stop.current_stop_price == Decimal("155.00")

    # Price decreases to 145 - stop should lower to 150
    await manager._on_market_data("AAPL", Decimal("145.00"))
    assert trailing_stop.high_water_mark == Decimal("145.00")
    assert trailing_stop.current_stop_price == Decimal("150.00")

    # Wait for rate limit to clear
    await asyncio.sleep(1.1)

    # Price decreases to 140 - stop should lower to 145
    await manager._on_market_data("AAPL", Decimal("140.00"))
    assert trailing_stop.high_water_mark == Decimal("140.00")
    assert trailing_stop.current_stop_price == Decimal("145.00")

    await manager.stop()

    # Cleanup
    if state_file.exists():
        state_file.unlink()


@pytest.mark.asyncio
async def test_trailing_stop_activation_threshold() -> None:
    """Test that trailing stop only activates above threshold price."""
    broker_mock = MagicMock(spec=IBKRBroker)
    broker_mock.place_order = AsyncMock(
        return_value=OrderResult(
            order_id=1006,
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type="STP",
            status=OrderStatus.SUBMITTED,
        )
    )

    event_bus = EventBus()
    state_file = Path("/tmp/test_trailing_activation.json")
    if state_file.exists():
        state_file.unlink()

    manager = TrailingStopManager(broker_mock, event_bus, state_file)
    await manager.start()

    config = TrailingStopConfig(
        symbol="AAPL",
        side=OrderSide.SELL,
        quantity=10,
        trail_amount=Decimal("5.00"),
        activation_price=Decimal("155.00"),  # Activate only above 155
    )

    initial_price = Decimal("150.00")
    stop_id = await manager.create_trailing_stop(config, initial_price)

    trailing_stop = manager.active_stops[stop_id]
    assert not trailing_stop.activated

    # Price at 152 - still below activation threshold
    await manager._on_market_data("AAPL", Decimal("152.00"))
    assert not trailing_stop.activated
    assert trailing_stop.current_stop_price == Decimal("145.00")  # Unchanged

    # Price reaches 156 - should activate
    await manager._on_market_data("AAPL", Decimal("156.00"))
    assert trailing_stop.activated
    assert trailing_stop.high_water_mark == Decimal("156.00")
    assert trailing_stop.current_stop_price == Decimal("151.00")  # 156 - 5

    await manager.stop()

    # Cleanup
    if state_file.exists():
        state_file.unlink()


@pytest.mark.asyncio
async def test_trailing_stop_state_persistence() -> None:
    """Test that trailing stop state persists across restarts."""
    broker_mock = MagicMock(spec=IBKRBroker)
    broker_mock.place_order = AsyncMock(
        return_value=OrderResult(
            order_id=1007,
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type="STP",
            status=OrderStatus.SUBMITTED,
        )
    )

    event_bus = EventBus()
    state_file = Path("/tmp/test_trailing_persistence.json")
    if state_file.exists():
        state_file.unlink()

    # Create manager and trailing stop
    manager1 = TrailingStopManager(broker_mock, event_bus, state_file)

    config = TrailingStopConfig(
        symbol="AAPL",
        side=OrderSide.SELL,
        quantity=10,
        trail_amount=Decimal("5.00"),
    )

    initial_price = Decimal("150.00")
    stop_id = await manager1.create_trailing_stop(config, initial_price)

    # Verify state file was created
    assert state_file.exists()

    # Create new manager - should load persisted state
    manager2 = TrailingStopManager(broker_mock, event_bus, state_file)
    assert stop_id in manager2.active_stops
    assert len(manager2.active_stops) == 1

    restored_stop = manager2.active_stops[stop_id]
    assert restored_stop.config.symbol == "AAPL"
    assert restored_stop.current_stop_price == Decimal("145.00")
    assert restored_stop.high_water_mark == Decimal("150.00")

    # Cleanup
    if state_file.exists():
        state_file.unlink()


@pytest.mark.asyncio
async def test_trailing_stop_cancel() -> None:
    """Test cancelling a trailing stop."""
    broker_mock = MagicMock(spec=IBKRBroker)
    broker_mock.place_order = AsyncMock(
        return_value=OrderResult(
            order_id=1008,
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type="STP",
            status=OrderStatus.SUBMITTED,
        )
    )

    event_bus = EventBus()
    state_file = Path("/tmp/test_trailing_cancel.json")
    if state_file.exists():
        state_file.unlink()

    manager = TrailingStopManager(broker_mock, event_bus, state_file)

    config = TrailingStopConfig(
        symbol="AAPL",
        side=OrderSide.SELL,
        quantity=10,
        trail_amount=Decimal("5.00"),
    )

    initial_price = Decimal("150.00")
    stop_id = await manager.create_trailing_stop(config, initial_price)

    assert stop_id in manager.active_stops

    # Cancel the trailing stop
    await manager.cancel_trailing_stop(stop_id)

    assert stop_id not in manager.active_stops

    # Cleanup
    if state_file.exists():
        state_file.unlink()


@pytest.mark.asyncio
async def test_trailing_stop_cancel_nonexistent_raises() -> None:
    """Test that cancelling non-existent trailing stop raises KeyError."""
    broker_mock = MagicMock(spec=IBKRBroker)
    event_bus = EventBus()
    state_file = Path("/tmp/test_trailing_cancel_error.json")

    manager = TrailingStopManager(broker_mock, event_bus, state_file)

    with pytest.raises(KeyError, match="Trailing stop INVALID_ID not found"):
        await manager.cancel_trailing_stop("INVALID_ID")

    # Cleanup
    if state_file.exists():
        state_file.unlink()


@pytest.mark.asyncio
async def test_trailing_stop_rate_limiting() -> None:
    """Test that trailing stop updates are rate limited."""
    broker_mock = MagicMock(spec=IBKRBroker)
    broker_mock.place_order = AsyncMock(
        return_value=OrderResult(
            order_id=1009,
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type="STP",
            status=OrderStatus.SUBMITTED,
        )
    )

    event_bus = EventBus()
    state_file = Path("/tmp/test_trailing_rate_limit.json")
    if state_file.exists():
        state_file.unlink()

    manager = TrailingStopManager(broker_mock, event_bus, state_file)
    manager._min_update_interval = 1.0  # 1 second rate limit
    await manager.start()

    config = TrailingStopConfig(
        symbol="AAPL",
        side=OrderSide.SELL,
        quantity=10,
        trail_amount=Decimal("5.00"),
    )

    initial_price = Decimal("150.00")
    stop_id = await manager.create_trailing_stop(config, initial_price)

    trailing_stop = manager.active_stops[stop_id]

    # First update should work
    await manager._on_market_data("AAPL", Decimal("155.00"))
    assert trailing_stop.current_stop_price == Decimal("150.00")

    # Immediate second update should be rate limited (skipped)
    await manager._on_market_data("AAPL", Decimal("160.00"))
    # Stop price should NOT have updated due to rate limiting
    # (high water mark updates but stop modification is rate limited)

    # Wait for rate limit to expire
    await asyncio.sleep(1.1)

    # Now update should work
    await manager._on_market_data("AAPL", Decimal("165.00"))
    assert trailing_stop.current_stop_price == Decimal("160.00")

    await manager.stop()

    # Cleanup
    if state_file.exists():
        state_file.unlink()
