"""Factor and signal utilities for advanced strategies."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal


@dataclass(slots=True)
class MomentumReading:
    signal: Decimal
    fast_mean: Decimal
    slow_mean: Decimal


def rolling_mean(window: Iterable[Decimal]) -> Decimal:
    values = list(window)
    if not values:
        return Decimal("0")
    return sum(values) / Decimal(len(values))


def momentum_signal(prices: deque[Decimal], fast: int, slow: int) -> MomentumReading:
    """Compute momentum using fast/slow moving averages."""
    if len(prices) < slow:
        return MomentumReading(signal=Decimal("0"), fast_mean=Decimal("0"), slow_mean=Decimal("0"))

    fast_window = list(prices)[-fast:]
    slow_window = list(prices)[-slow:]
    fast_mean = rolling_mean(fast_window)
    slow_mean = rolling_mean(slow_window)
    signal = fast_mean - slow_mean
    return MomentumReading(signal=signal, fast_mean=fast_mean, slow_mean=slow_mean)


def atr(
    prices: deque[Decimal], highs: deque[Decimal], lows: deque[Decimal], period: int
) -> Decimal:
    """Simplified ATR using high-low ranges."""
    if len(highs) < period or len(lows) < period:
        return Decimal("0")
    ranges = [highs[-i] - lows[-i] for i in range(1, period + 1)]
    return sum(ranges) / Decimal(len(ranges))
