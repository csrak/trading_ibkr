"""Compatibility shim for ibkr_trader.risk."""

from ibkr_trader.risk.guards import CorrelationMatrix, CorrelationRiskGuard
from ibkr_trader.risk.portfolio import (
    PortfolioSnapshot,
    PortfolioState,
    RiskGuard,
    SymbolLimitRegistry,
    SymbolLimits,
)

__all__ = [
    "PortfolioSnapshot",
    "PortfolioState",
    "RiskGuard",
    "SymbolLimitRegistry",
    "SymbolLimits",
    "CorrelationMatrix",
    "CorrelationRiskGuard",
]
