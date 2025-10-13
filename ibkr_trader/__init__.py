"""IBKR Personal Trader - A safe, modular trading platform."""

__version__ = "0.1.0"

from ibkr_trader.broker import IBKRBroker
from ibkr_trader.config import IBKRConfig, TradingMode, load_config
from ibkr_trader.models import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    SymbolContract,
)
from ibkr_trader.safety import LiveTradingGuard, LiveTradingError
from ibkr_trader.strategy import SMAConfig, SimpleMovingAverageStrategy, Strategy

__all__ = [
    "IBKRBroker",
    "IBKRConfig",
    "TradingMode",
    "load_config",
    "OrderRequest",
    "OrderResult",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "Position",
    "SymbolContract",
    "LiveTradingGuard",
    "LiveTradingError",
    "Strategy",
    "SimpleMovingAverageStrategy",
    "SMAConfig",
]