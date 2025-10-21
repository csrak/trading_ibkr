"""Compatibility shim for ibkr_trader.presets."""

from ibkr_trader.execution.presets import get_preset, preset_names

__all__ = ["get_preset", "preset_names"]
