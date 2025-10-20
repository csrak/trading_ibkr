"""Configuration objects for advanced strategies."""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field

from ibkr_trader.strategy import StrategyConfig


class AdaptiveMomentumConfig(StrategyConfig):
    """Configuration for the adaptive momentum/mean-reversion hybrid strategy."""

    fast_lookback: int = Field(default=5, description="Fast momentum lookback (bars).")
    slow_lookback: int = Field(default=20, description="Slow momentum lookback (bars).")
    reversion_lookback: int = Field(default=30, description="VWAP/reversion lookback (bars).")
    atr_lookback: int = Field(default=14, description="ATR window used for volatility sizing.")
    max_risk_fraction: Decimal = Field(
        default=Decimal("0.02"),
        description="Fraction of account equity to risk per position.",
    )
    min_edge_bps: Decimal = Field(
        default=Decimal("10"),
        description="Minimum expected edge (basis points) after fees to justify a trade.",
    )
    max_open_positions: int = Field(
        default=8, description="Maximum simultaneous positions across the universe."
    )
    screener_refresh_seconds: int = Field(
        default=900, description="How often to refresh the universe screen (seconds)."
    )
    telemetry_namespace: str = Field(
        default="adaptive_momentum", description="Telemetry namespace prefix."
    )
