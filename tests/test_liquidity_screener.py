"""Tests for LiquidityScreener integration with market data."""

import asyncio
from datetime import UTC, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pandas as pd

from ibkr_trader.data import LiquidityScreener, LiquidityScreenerConfig
from model.data.client import MarketDataClient
from model.data.market_data import PriceBarRequest


def test_liquidity_screener_mock_mode() -> None:
    """Test screener works without market data client (mock mode)."""
    config = LiquidityScreenerConfig(
        minimum_dollar_volume=Decimal("1000000"),
        minimum_price=Decimal("10"),
        max_symbols=5,
    )
    screener = LiquidityScreener(config)

    result = asyncio.run(screener.run())

    assert len(result.symbols) <= 5
    assert result.metadata is not None
    assert result.metadata["data_source"] == "mock"
    assert "universe_size" in result.metadata


def test_liquidity_screener_with_real_data() -> None:
    """Test screener with mocked MarketDataClient returning real-like data."""
    # Create mock client
    mock_client = Mock(spec=MarketDataClient)

    # Mock data for AAPL
    aapl_data = pd.DataFrame(
        {
            "close": [150.0, 152.0, 151.0, 153.0, 154.0],
            "volume": [1000000, 1100000, 1050000, 1200000, 1150000],
        },
        index=pd.date_range(start="2025-10-15", periods=5, freq="D", tz=UTC),
    )

    # Mock data for MSFT
    msft_data = pd.DataFrame(
        {
            "close": [300.0, 302.0, 301.0, 303.0, 305.0],
            "volume": [800000, 850000, 820000, 900000, 870000],
        },
        index=pd.date_range(start="2025-10-15", periods=5, freq="D", tz=UTC),
    )

    # Mock data for low-volume stock (should be filtered out)
    low_vol_data = pd.DataFrame(
        {
            "close": [50.0, 51.0, 50.5, 51.5, 52.0],
            "volume": [1000, 1100, 1050, 1200, 1150],  # Very low volume
        },
        index=pd.date_range(start="2025-10-15", periods=5, freq="D", tz=UTC),
    )

    def mock_get_bars(request: PriceBarRequest) -> pd.DataFrame:
        if request.symbol == "AAPL":
            return aapl_data.copy()
        if request.symbol == "MSFT":
            return msft_data.copy()
        if request.symbol == "LOWVOL":
            return low_vol_data.copy()
        return pd.DataFrame()  # Empty for unknown symbols

    mock_client.get_price_bars = mock_get_bars

    # Configure screener
    config = LiquidityScreenerConfig(
        universe=["AAPL", "MSFT", "LOWVOL"],
        minimum_dollar_volume=Decimal("100000000"),  # 100M
        minimum_price=Decimal("50"),
        lookback_days=5,
        max_symbols=10,
    )

    screener = LiquidityScreener(config, market_data_client=mock_client)
    result = asyncio.run(screener.run())

    # Should include AAPL and MSFT but not LOWVOL
    assert "AAPL" in result.symbols
    assert "MSFT" in result.symbols
    assert "LOWVOL" not in result.symbols  # Filtered out due to low dollar volume

    assert result.metadata is not None
    assert result.metadata["data_source"] == "real"
    assert result.metadata["lookback_days"] == 5


def test_liquidity_screener_handles_missing_data() -> None:
    """Test screener gracefully handles symbols with no data."""
    mock_client = Mock(spec=MarketDataClient)

    def mock_get_bars(request: PriceBarRequest) -> pd.DataFrame:
        # Return empty for all symbols
        return pd.DataFrame()

    mock_client.get_price_bars = mock_get_bars

    config = LiquidityScreenerConfig(
        universe=["INVALID1", "INVALID2"],
        minimum_dollar_volume=Decimal("1000000"),
        minimum_price=Decimal("10"),
        lookback_days=5,
    )

    screener = LiquidityScreener(config, market_data_client=mock_client)
    result = asyncio.run(screener.run())

    # Should return empty result when no data available
    assert len(result.symbols) == 0
    assert result.metadata["data_source"] == "real"


def test_liquidity_screener_sorts_by_dollar_volume() -> None:
    """Test that screener sorts results by dollar volume descending."""
    mock_client = Mock(spec=MarketDataClient)

    # High dollar volume stock
    high_vol = pd.DataFrame(
        {"close": [500.0] * 5, "volume": [2000000] * 5},
        index=pd.date_range(start="2025-10-15", periods=5, freq="D", tz=UTC),
    )

    # Medium dollar volume stock
    med_vol = pd.DataFrame(
        {"close": [100.0] * 5, "volume": [1000000] * 5},
        index=pd.date_range(start="2025-10-15", periods=5, freq="D", tz=UTC),
    )

    # Low dollar volume stock
    low_vol = pd.DataFrame(
        {"close": [50.0] * 5, "volume": [500000] * 5},
        index=pd.date_range(start="2025-10-15", periods=5, freq="D", tz=UTC),
    )

    def mock_get_bars(request: PriceBarRequest) -> pd.DataFrame:
        if request.symbol == "HIGH":
            return high_vol.copy()
        if request.symbol == "MED":
            return med_vol.copy()
        if request.symbol == "LOW":
            return low_vol.copy()
        return pd.DataFrame()

    mock_client.get_price_bars = mock_get_bars

    config = LiquidityScreenerConfig(
        universe=["LOW", "HIGH", "MED"],  # Intentionally unordered
        minimum_dollar_volume=Decimal("10000000"),  # 10M - all should pass
        minimum_price=Decimal("1"),
        lookback_days=5,
        max_symbols=3,
    )

    screener = LiquidityScreener(config, market_data_client=mock_client)
    result = asyncio.run(screener.run())

    # Should be sorted by dollar volume: HIGH > MED > LOW
    assert result.symbols == ["HIGH", "MED", "LOW"]


def test_liquidity_screener_is_stale() -> None:
    """Test staleness checking."""
    config = LiquidityScreenerConfig()
    screener = LiquidityScreener(config)

    # Should be stale when never run
    assert screener.is_stale(timedelta(minutes=15))

    # Run screener
    asyncio.run(screener.run())

    # Should not be stale immediately after
    assert not screener.is_stale(timedelta(hours=1))

    # Should be stale with zero TTL
    assert screener.is_stale(timedelta(seconds=0))
