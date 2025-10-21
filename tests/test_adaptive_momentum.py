"""Tests for AdaptiveMomentumStrategy screener scheduler."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from ibkr_trader.data import Screener, ScreenerResult
from ibkr_trader.events import EventBus
from ibkr_trader.strategies.adaptive_momentum import AdaptiveMomentumStrategy
from ibkr_trader.strategies.config import AdaptiveMomentumConfig


@pytest.fixture
def mock_broker() -> Mock:
    """Create a mock broker."""
    broker = Mock()
    broker.get_position = AsyncMock(return_value=0)
    return broker


@pytest.fixture
def event_bus() -> EventBus:
    """Create an event bus."""
    return EventBus()


@pytest.fixture
def strategy_config() -> AdaptiveMomentumConfig:
    """Create strategy configuration."""
    return AdaptiveMomentumConfig(
        name="test_strategy",
        symbols=["AAPL", "MSFT"],
        screener_refresh_seconds=1,  # Fast refresh for testing
    )


@pytest.fixture
def mock_screener() -> Mock:
    """Create a mock screener."""
    screener = Mock(spec=Screener)
    screener.run = AsyncMock(
        return_value=ScreenerResult(
            symbols=["AAPL", "GOOGL", "TSLA"],
            generated_at=datetime.now(UTC),
            metadata={"test": True},
        )
    )
    return screener


@pytest.mark.asyncio
async def test_screener_task_starts_when_screener_set(
    strategy_config: AdaptiveMomentumConfig,
    mock_broker: Mock,
    event_bus: EventBus,
    mock_screener: Mock,
) -> None:
    """Test that screener refresh task starts when screener is set."""
    strategy = AdaptiveMomentumStrategy(strategy_config, mock_broker, event_bus)
    strategy.set_screener(mock_screener)

    await strategy.start()

    # Task should be created
    assert strategy._screener_task is not None
    assert not strategy._screener_task.done()

    await strategy.stop()


@pytest.mark.asyncio
async def test_screener_task_not_started_without_screener(
    strategy_config: AdaptiveMomentumConfig,
    mock_broker: Mock,
    event_bus: EventBus,
) -> None:
    """Test that screener refresh task doesn't start when screener is None."""
    strategy = AdaptiveMomentumStrategy(strategy_config, mock_broker, event_bus)

    await strategy.start()

    # Task should not be created
    assert strategy._screener_task is None

    await strategy.stop()


@pytest.mark.asyncio
async def test_screener_task_not_started_when_interval_zero(
    mock_broker: Mock,
    event_bus: EventBus,
    mock_screener: Mock,
) -> None:
    """Test that screener refresh task doesn't start when refresh interval is 0."""
    config = AdaptiveMomentumConfig(
        name="test_strategy",
        symbols=["AAPL"],
        screener_refresh_seconds=0,  # Disabled
    )
    strategy = AdaptiveMomentumStrategy(config, mock_broker, event_bus)
    strategy.set_screener(mock_screener)

    await strategy.start()

    # Task should not be created
    assert strategy._screener_task is None

    await strategy.stop()


@pytest.mark.asyncio
async def test_screener_refresh_called_periodically(
    strategy_config: AdaptiveMomentumConfig,
    mock_broker: Mock,
    event_bus: EventBus,
    mock_screener: Mock,
) -> None:
    """Test that screener refresh is called periodically."""
    strategy = AdaptiveMomentumStrategy(strategy_config, mock_broker, event_bus)
    strategy.set_screener(mock_screener)

    await strategy.start()

    # Wait for multiple refresh cycles (1 second interval)
    await asyncio.sleep(2.5)

    # Should have been called at least twice
    assert mock_screener.run.call_count >= 2

    await strategy.stop()


@pytest.mark.asyncio
async def test_screener_refresh_updates_symbols(
    strategy_config: AdaptiveMomentumConfig,
    mock_broker: Mock,
    event_bus: EventBus,
    mock_screener: Mock,
) -> None:
    """Test that screener refresh updates the strategy's symbol universe."""
    strategy = AdaptiveMomentumStrategy(strategy_config, mock_broker, event_bus)
    strategy.set_screener(mock_screener)

    # Initial symbols
    assert strategy._symbols == {"AAPL", "MSFT"}

    await strategy.start()

    # Wait for at least one refresh
    await asyncio.sleep(1.5)

    # Symbols should be updated from screener result
    assert strategy._symbols == {"AAPL", "GOOGL", "TSLA"}

    await strategy.stop()


@pytest.mark.asyncio
async def test_screener_task_cancelled_on_stop(
    strategy_config: AdaptiveMomentumConfig,
    mock_broker: Mock,
    event_bus: EventBus,
    mock_screener: Mock,
) -> None:
    """Test that screener refresh task is cancelled when strategy stops."""
    strategy = AdaptiveMomentumStrategy(strategy_config, mock_broker, event_bus)
    strategy.set_screener(mock_screener)

    await strategy.start()

    task = strategy._screener_task
    assert task is not None
    assert not task.done()

    await strategy.stop()

    # Task should be cancelled and cleaned up
    assert task.done()
    assert task.cancelled()
    assert strategy._screener_task is None


@pytest.mark.asyncio
async def test_screener_error_handling(
    strategy_config: AdaptiveMomentumConfig,
    mock_broker: Mock,
    event_bus: EventBus,
) -> None:
    """Test that screener errors are caught and logged without crashing."""
    # Create screener that raises error
    failing_screener = Mock(spec=Screener)
    failing_screener.run = AsyncMock(side_effect=RuntimeError("Screener failed"))

    strategy = AdaptiveMomentumStrategy(strategy_config, mock_broker, event_bus)
    strategy.set_screener(failing_screener)

    await strategy.start()

    # Wait for a few refresh attempts
    await asyncio.sleep(2.5)

    # Task should still be running despite errors
    assert strategy._screener_task is not None
    assert not strategy._screener_task.done()

    # Should have attempted multiple times
    assert failing_screener.run.call_count >= 2

    await strategy.stop()


@pytest.mark.asyncio
async def test_screener_retains_symbols_on_empty_result(
    strategy_config: AdaptiveMomentumConfig,
    mock_broker: Mock,
    event_bus: EventBus,
) -> None:
    """Test that empty screener results don't clear the symbol universe."""
    # Create screener that returns empty result
    empty_screener = Mock(spec=Screener)
    empty_screener.run = AsyncMock(
        return_value=ScreenerResult(
            symbols=[],  # Empty!
            generated_at=datetime.now(UTC),
            metadata={},
        )
    )

    strategy = AdaptiveMomentumStrategy(strategy_config, mock_broker, event_bus)
    strategy.set_screener(empty_screener)

    original_symbols = strategy._symbols.copy()

    await strategy.start()

    # Wait for refresh
    await asyncio.sleep(1.5)

    # Symbols should be unchanged
    assert strategy._symbols == original_symbols

    await strategy.stop()
