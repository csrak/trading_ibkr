"""Custom exceptions for strategy coordinator operations."""

from __future__ import annotations


class StrategyCoordinatorError(Exception):
    """Base error for strategy coordinator failures."""


class StrategyInitializationError(StrategyCoordinatorError):
    """Raised when a strategy cannot be initialized."""


class CapitalAllocationError(StrategyCoordinatorError):
    """Raised when the capital allocation policy fails."""
