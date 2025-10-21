"""Compatibility shim for ibkr_trader.events.

The canonical event bus module now lives under ibkr_trader.core.events.
Importing from this module is still supported for backwards compatibility.
"""

from ibkr_trader.core.events import (
    DiagnosticEvent,
    EventBus,
    EventSubscription,
    EventTopic,
    ExecutionEvent,
    MarketDataEvent,
    OrderStatusEvent,
)

__all__ = [
    "DiagnosticEvent",
    "EventBus",
    "EventSubscription",
    "EventTopic",
    "ExecutionEvent",
    "MarketDataEvent",
    "OrderStatusEvent",
]
