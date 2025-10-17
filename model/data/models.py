"""Shared data structures used across simulation and storage utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def _ensure_utc(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


class BookSide(str, Enum):
    BID = "bid"
    ASK = "ask"


@dataclass(slots=True)
class OrderBookLevel:
    side: BookSide
    price: float
    size: float
    level: int
    num_orders: int | None = None

    def to_record(self, timestamp: datetime, symbol: str, venue: str | None) -> dict[str, Any]:
        return {
            "timestamp": timestamp.isoformat(),
            "symbol": symbol,
            "side": self.side.value,
            "price": self.price,
            "size": self.size,
            "level": self.level,
            "num_orders": self.num_orders,
            "venue": venue,
        }


@dataclass(slots=True)
class OrderBookSnapshot:
    timestamp: datetime
    symbol: str
    levels: list[OrderBookLevel]
    venue: str | None = None

    def __post_init__(self) -> None:
        self.timestamp = _ensure_utc(self.timestamp)
        self.symbol = self.symbol.upper().strip()


@dataclass(slots=True)
class TradeEvent:
    timestamp: datetime
    symbol: str
    price: float
    size: float
    side: str
    venue: str | None = None

    def __post_init__(self) -> None:
        self.timestamp = _ensure_utc(self.timestamp)
        self.symbol = self.symbol.upper().strip()

    def to_record(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "price": self.price,
            "size": self.size,
            "side": self.side,
            "venue": self.venue,
        }


class OptionRight(str, Enum):
    CALL = "C"
    PUT = "P"


@dataclass(slots=True)
class OptionSurfaceEntry:
    timestamp: datetime
    symbol: str
    expiry: str
    strike: float
    right: OptionRight
    bid: float
    ask: float
    mid: float | None = None
    last: float | None = None
    implied_vol: float | None = None
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None
    underlying_price: float | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        self.timestamp = _ensure_utc(self.timestamp)
        self.symbol = self.symbol.upper().strip()

    def to_record(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "expiry": self.expiry,
            "strike": self.strike,
            "right": self.right.value,
            "bid": self.bid,
            "ask": self.ask,
            "mid": self.mid,
            "last": self.last,
            "implied_vol": self.implied_vol,
            "delta": self.delta,
            "gamma": self.gamma,
            "vega": self.vega,
            "theta": self.theta,
            "underlying_price": self.underlying_price,
            "source": self.source,
        }


class OrderStatus(str, Enum):
    WORKING = "working"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class OrderStateSnapshot:
    order_id: str
    status: OrderStatus
    submitted_at: datetime
    updated_at: datetime | None = None
    filled_qty: float | None = None
    remaining_qty: float | None = None
    avg_price: float | None = None
    venue: str | None = None

    def __post_init__(self) -> None:
        self.submitted_at = _ensure_utc(self.submitted_at)
        if self.updated_at is not None:
            self.updated_at = _ensure_utc(self.updated_at)
