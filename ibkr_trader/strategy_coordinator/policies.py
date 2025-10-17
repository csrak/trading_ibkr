"""Capital allocation policies for the strategy coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from ibkr_trader.strategy_configs.graph import (
    CapitalPolicyConfig,
    StrategyGraphConfig,
)


@dataclass(slots=True)
class PositionEnvelope:
    """Defines sizing limits allocated to a strategy for a symbol."""

    max_position: int | None = None
    max_notional: Decimal | None = None


class CapitalAllocationPolicy(Protocol):
    """Interface for capital allocation strategies."""

    def prepare(self, graph: StrategyGraphConfig) -> None:
        """Initialize internal state based on graph configuration."""

    def envelope_for(self, strategy_id: str, symbol: str) -> PositionEnvelope:
        """Return the sizing envelope for a given strategy and symbol."""


class EqualWeightPolicy:
    """Simple policy that assigns identical envelopes to each strategy."""

    def __init__(self, config: CapitalPolicyConfig) -> None:
        self._config = config
        self._envelopes: dict[tuple[str, str], PositionEnvelope] = {}

    def prepare(self, graph: StrategyGraphConfig) -> None:
        envelopes: dict[tuple[str, str], PositionEnvelope] = {}
        for node in graph.strategies:
            for symbol in node.symbols:
                envelopes[(node.id, symbol)] = PositionEnvelope(
                    max_position=node.max_position,
                    max_notional=node.max_notional,
                )
        self._envelopes = envelopes

    def envelope_for(self, strategy_id: str, symbol: str) -> PositionEnvelope:
        return self._envelopes.get((strategy_id, symbol), PositionEnvelope())
