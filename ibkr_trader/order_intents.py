"""Core order intent definitions shared by strategies and the coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

TARGET_POSITION = "target_position"
MARKET_DELTA = "market_delta"


@dataclass(slots=True, frozen=True)
class OrderIntent:
    """Represents a strategy's desired exposure adjustment."""

    strategy_id: str
    symbol: str
    intent_type: str
    quantity: int
    timestamp: datetime
    metadata: dict[str, object] | None = None
