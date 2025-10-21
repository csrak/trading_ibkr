"""Liquidity-based screener implementation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from pydantic import BaseModel, Field

from model.data.client import MarketDataClient
from model.data.market_data import PriceBarRequest

from .base import Screener, ScreenerResult


class LiquidityScreenerConfig(BaseModel):
    minimum_dollar_volume: Decimal = Field(
        default=Decimal("5000000"),
        description="Minimum average daily dollar volume required.",
    )
    minimum_price: Decimal = Field(default=Decimal("5"), description="Minimum share price.")
    universe: Sequence[str] = Field(
        default_factory=list,
        description="Optional static universe to filter; empty means fetch from data source.",
    )
    lookback_days: int = Field(default=20, description="Days used to compute averages.")
    max_symbols: int = Field(default=20, description="Maximum symbols to return.")


@dataclass(slots=True)
class LiquiditySnapshot:
    symbol: str
    average_dollar_volume: Decimal
    average_price: Decimal


class LiquidityScreener(Screener):
    """Liquidity-based screener using real market data or mock data as fallback.

    When a MarketDataClient is provided, calculates real average dollar volume
    and price from historical bars. Otherwise falls back to mock data.
    """

    def __init__(
        self, config: LiquidityScreenerConfig, market_data_client: MarketDataClient | None = None
    ) -> None:
        self.config = config
        self.market_data_client = market_data_client
        self._last_run: datetime | None = None

    async def run(self) -> ScreenerResult:
        """Run liquidity screening on universe.

        Uses real market data if client is available, otherwise falls back to mock data.
        """
        logger.debug(
            "Running liquidity screener with min_volume={} min_price={}",
            self.config.minimum_dollar_volume,
            self.config.minimum_price,
        )

        # Fetch real data if client is available, otherwise use mock
        if self.market_data_client is not None:
            candidates = self._real_liquidity_feed()
        else:
            logger.debug("No market data client provided, using mock data")
            candidates = self._mock_liquidity_feed()

        # Filter by thresholds
        filtered = [
            snap.symbol
            for snap in candidates
            if snap.average_dollar_volume >= self.config.minimum_dollar_volume
            and snap.average_price >= self.config.minimum_price
        ]
        filtered = filtered[: self.config.max_symbols]

        self._last_run = datetime.now(UTC)
        return ScreenerResult(
            symbols=filtered,
            generated_at=self._last_run,
            metadata={
                "universe_size": len(filtered),
                "lookback_days": self.config.lookback_days,
                "stale": False,
                "data_source": "real" if self.market_data_client is not None else "mock",
            },
        )

    def is_stale(self, ttl: timedelta) -> bool:
        if self._last_run is None:
            return True
        return datetime.now(UTC) - self._last_run > ttl

    def _real_liquidity_feed(self) -> list[LiquiditySnapshot]:
        """Fetch real market data and calculate liquidity metrics."""
        assert self.market_data_client is not None

        # Determine universe to screen
        universe = list(self.config.universe) if self.config.universe else self._default_universe()

        # Calculate date range for lookback
        end = datetime.now(UTC)
        start = end - timedelta(days=self.config.lookback_days)

        snapshots: list[LiquiditySnapshot] = []

        for symbol in universe:
            try:
                # Fetch historical bars
                request = PriceBarRequest(
                    symbol=symbol, start=start, end=end, interval="1d", auto_adjust=True
                )
                bars = self.market_data_client.get_price_bars(request)

                if bars.empty:
                    logger.debug("No data available for {}", symbol)
                    continue

                # Calculate metrics from dataframe
                # Expected columns: close, volume (after normalization)
                if "close" in bars.columns and "volume" in bars.columns:
                    avg_price = Decimal(str(bars["close"].mean()))
                    # Dollar volume = price * volume, then average
                    dollar_volumes = bars["close"] * bars["volume"]
                    avg_dollar_volume = Decimal(str(dollar_volumes.mean()))

                    snapshots.append(
                        LiquiditySnapshot(
                            symbol=symbol,
                            average_dollar_volume=avg_dollar_volume,
                            average_price=avg_price,
                        )
                    )
                else:
                    logger.warning(
                        "Missing required columns for {} (have: {})", symbol, list(bars.columns)
                    )

            except Exception as exc:
                logger.warning("Failed to fetch data for {}: {}", symbol, exc)
                continue

        # Sort by dollar volume descending
        snapshots.sort(key=lambda s: s.average_dollar_volume, reverse=True)
        return snapshots

    def _default_universe(self) -> list[str]:
        """Default universe when config.universe is empty."""
        # Common liquid US equities
        return [
            "AAPL",
            "MSFT",
            "NVDA",
            "AMZN",
            "META",
            "GOOGL",
            "TSLA",
            "BRK.B",
            "JPM",
            "V",
            "UNH",
            "JNJ",
            "WMT",
            "XOM",
            "PG",
            "MA",
            "HD",
            "CVX",
            "MRK",
            "ABBV",
        ]

    def _mock_liquidity_feed(self) -> list[LiquiditySnapshot]:
        universe = self.config.universe or ["AAPL", "MSFT", "NVDA", "AMZN", "META"]
        baseline = Decimal("10000000")
        snapshots: list[LiquiditySnapshot] = []
        for idx, symbol in enumerate(universe, start=1):
            factor = Decimal(idx) / Decimal(len(universe))
            snapshots.append(
                LiquiditySnapshot(
                    symbol=symbol,
                    average_dollar_volume=baseline * factor,
                    average_price=Decimal("150") * factor,
                )
            )
        return snapshots
