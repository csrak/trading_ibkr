"""Base interfaces for symbol screeners."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(slots=True)
class ScreenerResult:
    symbols: Sequence[str]
    generated_at: datetime
    metadata: dict[str, object] | None = None


class Screener(Protocol):
    """Protocol for screener implementations."""

    async def run(self) -> ScreenerResult: ...
