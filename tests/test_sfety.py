"""Tests for trading safety guards."""

import pytest

from ibkr_trader.config import IBKRConfig, TradingMode
from ibkr_trader.safety import LiveTradingError, LiveTradingGuard


def test_paper_trading_guard_allows_trading() -> None:
    """Test that paper trading mode allows trading without live flag."""
    config = IBKRConfig(trading_mode=TradingMode.PAPER)
    guard = LiveTradingGuard(config=config, live_flag_enabled=False)

    # Should not raise
    guard.validate_trading_mode()
    guard.acknowledge_live_trading()

    assert guard.is_paper_trading
    assert not guard.is_live_trading


def test_live_trading_requires_flag() -> None:
    """Test that live trading requires --live flag."""
    config = IBKRConfig(trading_mode=TradingMode.LIVE)
    guard = LiveTradingGuard(config=config, live_flag_enabled=False)

    with pytest.raises(LiveTradingError) as exc_info:
        guard.validate_trading_mode()

    assert "--live flag not provided" in str(exc_info.value)


def test_live_trading_requires_acknowledgment() -> None:
    """Test that live trading requires explicit acknowledgment."""
    config = IBKRConfig(trading_mode=TradingMode.LIVE)
    guard = LiveTradingGuard(config=config, live_flag_enabled=True)

    # Should fail without acknowledgment
    with pytest.raises(LiveTradingError) as exc_info:
        guard.validate_trading_mode()

    assert "explicitly acknowledged" in str(exc_info.value)


def test_live_trading_full_flow() -> None:
    """Test complete live trading authorization flow."""
    config = IBKRConfig(trading_mode=TradingMode.LIVE, port=7496)
    guard = LiveTradingGuard(config=config, live_flag_enabled=True)

    # Acknowledge live trading
    guard.acknowledge_live_trading()

    # Now validation should pass
    guard.validate_trading_mode()

    assert guard.is_live_trading
    assert not guard.is_paper_trading


def test_order_safety_checks() -> None:
    """Test order safety validations."""
    config = IBKRConfig(trading_mode=TradingMode.PAPER, max_position_size=100)
    guard = LiveTradingGuard(config=config)

    # Should pass
    guard.check_order_safety(symbol="AAPL", quantity=50)

    # Should fail - exceeds max position size
    with pytest.raises(LiveTradingError) as exc_info:
        guard.check_order_safety(symbol="AAPL", quantity=150)

    assert "exceeds max position size" in str(exc_info.value)


def test_port_warning_in_live_mode() -> None:
    """Test warning when using paper port in live mode."""
    config = IBKRConfig(trading_mode=TradingMode.LIVE, port=7497)  # Paper port
    guard = LiveTradingGuard(config=config, live_flag_enabled=True)

    guard.acknowledge_live_trading()

    # Should validate but log warning (test that it doesn't raise)
    guard.validate_trading_mode()
