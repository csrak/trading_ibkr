"""Liquidity-based screener implementation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from pydantic import BaseModel, Field

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
    """Placeholder liquidity screener. Uses static inputs until data backend is connected."""

    def __init__(self, config: LiquidityScreenerConfig) -> None:
        self.config = config
        self._last_run: datetime | None = None

    async def run(self) -> ScreenerResult:
        # TODO: Integrate with real market data cache.
        logger.debug(
            "Running liquidity screener with min_volume={} min_price={}",
            self.config.minimum_dollar_volume,
            self.config.minimum_price,
        )

        candidates = self._mock_liquidity_feed()
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
            },
        )

    def is_stale(self, ttl: timedelta) -> bool:
        if self._last_run is None:
            return True
        return datetime.now(UTC) - self._last_run > ttl

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
