"""Compatibility shim for ibkr_trader.trailing_stops."""

from ibkr_trader.execution.trailing_stops import (
    TrailingStop,
    TrailingStopConfig,
    TrailingStopManager,
)

__all__ = [
    "TrailingStopConfig",
    "TrailingStop",
    "TrailingStopManager",
]
