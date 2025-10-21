"""Tests for factor calculation functions."""

from collections import deque
from decimal import Decimal

from ibkr_trader.strategies.factors import atr, momentum_signal, vwap


def test_momentum_signal_insufficient_data() -> None:
    """Test momentum signal with insufficient data returns zeros."""
    prices = deque([Decimal("100")], maxlen=100)
    result = momentum_signal(prices, fast=5, slow=20)

    assert result.signal == Decimal("0")
    assert result.fast_mean == Decimal("0")
    assert result.slow_mean == Decimal("0")


def test_momentum_signal_crossover() -> None:
    """Test momentum signal detects fast > slow crossover."""
    # Uptrend: prices rising
    prices = deque([Decimal(str(100 + i)) for i in range(25)], maxlen=100)

    result = momentum_signal(prices, fast=5, slow=20)

    # Fast SMA should be higher than slow SMA (positive signal)
    assert result.signal > 0
    assert result.fast_mean > result.slow_mean


def test_momentum_signal_downtrend() -> None:
    """Test momentum signal detects fast < slow in downtrend."""
    # Downtrend: prices falling
    prices = deque([Decimal(str(200 - i)) for i in range(25)], maxlen=100)

    result = momentum_signal(prices, fast=5, slow=20)

    # Fast SMA should be lower than slow SMA (negative signal)
    assert result.signal < 0
    assert result.fast_mean < result.slow_mean


def test_atr_insufficient_data() -> None:
    """Test ATR with insufficient data returns zero."""
    prices = deque([Decimal("100")], maxlen=100)
    highs = deque([Decimal("101")], maxlen=100)
    lows = deque([Decimal("99")], maxlen=100)

    result = atr(prices, highs, lows, period=14)

    assert result == Decimal("0")


def test_atr_calculation() -> None:
    """Test ATR calculation with valid data."""
    # Create data with known range
    prices = deque([Decimal("100")] * 20, maxlen=100)
    highs = deque([Decimal("102")] * 20, maxlen=100)  # Range of 3
    lows = deque([Decimal("99")] * 20, maxlen=100)

    result = atr(prices, highs, lows, period=14)

    # ATR should be average of ranges = 3
    assert result == Decimal("3")


def test_vwap_insufficient_data() -> None:
    """Test VWAP with insufficient data returns zero."""
    prices = deque([Decimal("100")], maxlen=100)
    highs = deque([Decimal("101")], maxlen=100)
    lows = deque([Decimal("99")], maxlen=100)
    volumes = deque([1000], maxlen=100)

    result = vwap(prices, highs, lows, volumes, period=20)

    assert result == Decimal("0")


def test_vwap_zero_volume() -> None:
    """Test VWAP with zero volume returns zero."""
    prices = deque([Decimal("100")] * 25, maxlen=100)
    highs = deque([Decimal("101")] * 25, maxlen=100)
    lows = deque([Decimal("99")] * 25, maxlen=100)
    volumes = deque([0] * 25, maxlen=100)  # Zero volume

    result = vwap(prices, highs, lows, volumes, period=20)

    assert result == Decimal("0")


def test_vwap_uniform_prices_and_volume() -> None:
    """Test VWAP with uniform prices and volume."""
    # All bars: close=100, high=101, low=99, volume=1000
    # Typical price = (101 + 99 + 100) / 3 = 100
    # VWAP = (100 * 1000 * 20) / (1000 * 20) = 100

    prices = deque([Decimal("100")] * 25, maxlen=100)
    highs = deque([Decimal("101")] * 25, maxlen=100)
    lows = deque([Decimal("99")] * 25, maxlen=100)
    volumes = deque([1000] * 25, maxlen=100)

    result = vwap(prices, highs, lows, volumes, period=20)

    # Expected: typical price = 100
    assert result == Decimal("100")


def test_vwap_weighted_by_volume() -> None:
    """Test VWAP correctly weights by volume."""
    # Last 3 bars:
    # Bar 1: close=100, high=101, low=99, volume=1000 -> typical = 100
    # Bar 2: close=110, high=111, low=109, volume=2000 -> typical = 110
    # Bar 3: close=120, high=121, low=119, volume=1000 -> typical = 120
    #
    # VWAP = (100*1000 + 110*2000 + 120*1000) / (1000 + 2000 + 1000)
    #      = (100000 + 220000 + 120000) / 4000
    #      = 440000 / 4000
    #      = 110

    prices = deque([Decimal("100"), Decimal("110"), Decimal("120")], maxlen=100)
    highs = deque([Decimal("101"), Decimal("111"), Decimal("121")], maxlen=100)
    lows = deque([Decimal("99"), Decimal("109"), Decimal("119")], maxlen=100)
    volumes = deque([1000, 2000, 1000], maxlen=100)

    result = vwap(prices, highs, lows, volumes, period=3)

    assert result == Decimal("110")


def test_vwap_handles_decimal_volumes() -> None:
    """Test VWAP handles volume conversion to Decimal."""
    prices = deque([Decimal("100")] * 25, maxlen=100)
    highs = deque([Decimal("101")] * 25, maxlen=100)
    lows = deque([Decimal("99")] * 25, maxlen=100)
    volumes = deque([500] * 25, maxlen=100)  # Integer volumes

    result = vwap(prices, highs, lows, volumes, period=10)

    # Should handle integer volumes without error
    assert result == Decimal("100")


def test_vwap_different_lookback_periods() -> None:
    """Test VWAP with different lookback periods."""
    # Create trend: last 20 bars have higher prices than earlier bars
    prices = deque([Decimal("100")] * 10 + [Decimal("110")] * 15, maxlen=100)
    highs = deque([Decimal("101")] * 10 + [Decimal("111")] * 15, maxlen=100)
    lows = deque([Decimal("99")] * 10 + [Decimal("109")] * 15, maxlen=100)
    volumes = deque([1000] * 25, maxlen=100)

    # Short period (recent bars, higher prices)
    vwap_short = vwap(prices, highs, lows, volumes, period=10)

    # Long period (mix of old and new bars)
    vwap_long = vwap(prices, highs, lows, volumes, period=20)

    # Short period VWAP should be higher (more recent high-price bars)
    assert vwap_short > vwap_long
