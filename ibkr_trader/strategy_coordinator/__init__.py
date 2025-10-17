"""Public API for the strategy coordinator package."""

from .coordinator import StrategyCoordinator
from .errors import StrategyCoordinatorError
from .policies import CapitalAllocationPolicy, EqualWeightPolicy, PositionEnvelope
from .wrapper import StrategyHandle, StrategyWrapper

__all__ = [
    "StrategyCoordinator",
    "StrategyCoordinatorError",
    "CapitalAllocationPolicy",
    "EqualWeightPolicy",
    "PositionEnvelope",
    "StrategyHandle",
    "StrategyWrapper",
]
