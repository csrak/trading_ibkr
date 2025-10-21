"""Execution layer modules (brokers, order management, presets)."""

from .broker import IBKRBroker
from .oco_orders import OCOOrderManager, OCOPair
from .presets import get_preset, preset_names
from .trailing_stops import TrailingStop, TrailingStopConfig, TrailingStopManager

__all__ = [
    "IBKRBroker",
    "OCOOrderManager",
    "OCOPair",
    "TrailingStopConfig",
    "TrailingStop",
    "TrailingStopManager",
    "get_preset",
    "preset_names",
]
