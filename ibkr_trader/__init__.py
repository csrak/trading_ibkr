"""IBKR Personal Trader - A safe, modular trading platform."""

__version__ = "0.1.0"

from ibkr_trader.backtest.engine import BacktestEngine
from ibkr_trader.broker import IBKRBroker
from ibkr_trader.config import IBKRConfig, TradingMode, load_config
from ibkr_trader.constants import (
    DEFAULT_PORTFOLIO_SNAPSHOT,
    MOCK_PRICE_BASE,
    MOCK_PRICE_SLEEP_SECONDS,
    MOCK_PRICE_VARIATION_MODULO,
    SUBSCRIPTION_SOFT_LIMIT,
)
from ibkr_trader.market_data import MarketDataService
from ibkr_trader.models import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    SymbolContract,
)
from ibkr_trader.portfolio import PortfolioState, RiskGuard
from ibkr_trader.safety import LiveTradingError, LiveTradingGuard
from ibkr_trader.sim.broker import SimulatedBroker, SimulatedMarketData
from ibkr_trader.strategy import (
    IndustryModelConfig,
    IndustryModelStrategy,
    SimpleMovingAverageStrategy,
    SMAConfig,
    Strategy,
)

__all__ = [
    "BacktestEngine",
    "IBKRBroker",
    "IBKRConfig",
    "TradingMode",
    "load_config",
    "SUBSCRIPTION_SOFT_LIMIT",
    "MOCK_PRICE_BASE",
    "MOCK_PRICE_VARIATION_MODULO",
    "MOCK_PRICE_SLEEP_SECONDS",
    "DEFAULT_PORTFOLIO_SNAPSHOT",
    "MarketDataService",
    "OrderRequest",
    "OrderResult",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "Position",
    "SymbolContract",
    "PortfolioState",
    "RiskGuard",
    "LiveTradingGuard",
    "LiveTradingError",
    "Strategy",
    "SimpleMovingAverageStrategy",
    "SMAConfig",
    "IndustryModelConfig",
    "IndustryModelStrategy",
    "SimulatedBroker",
    "SimulatedMarketData",
]
