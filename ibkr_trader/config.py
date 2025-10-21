"""Compatibility shim for ibkr_trader.config.

The canonical configuration module now lives under ibkr_trader.core.config.
Importing from this module is still supported for backwards compatibility.
"""

from ibkr_trader.core.config import IBKRConfig, TradingMode, load_config

__all__ = ["IBKRConfig", "TradingMode", "load_config"]
