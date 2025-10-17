"""Abstractions for retrieving historical price bar data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import pandas as pd


@dataclass(slots=True)
class PriceBarRequest:
    """Describes a historical bar query.

    Attributes:
        symbol: Ticker symbol (case-insensitive).
        start: Inclusive UTC timestamp for the window.
        end: Exclusive UTC timestamp for the window.
        interval: Bar interval recognised by the upstream source (default: ``1d``).
        auto_adjust: Whether the upstream source should auto-adjust prices.
    """

    symbol: str
    start: datetime
    end: datetime
    interval: str = "1d"
    auto_adjust: bool = True

    def __post_init__(self) -> None:
        self.symbol = self.symbol.upper().strip()
        self.start = _ensure_utc(self.start)
        self.end = _ensure_utc(self.end)
        if self.end <= self.start:
            raise ValueError("PriceBarRequest.end must be after start")


class MarketDataSource(Protocol):
    """Protocol implemented by concrete historical data sources."""

    def get_price_bars(self, request: PriceBarRequest) -> pd.DataFrame:
        """Return a dataframe of historical bars indexed by timestamp."""


def _ensure_utc(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def normalize_price_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a dataframe with lower-cased, underscore separated column names.

    The function is intentionally conservative: it only renames columns when
    they can be mapped to a simple ``snake_case`` representation.  Unknown
    columns are left untouched.
    """

    mapping: dict[str, str] = {}
    for column in frame.columns:
        normalized = str(column).strip().lower().replace(" ", "_")
        mapping[column] = normalized
    return frame.rename(columns=mapping)
