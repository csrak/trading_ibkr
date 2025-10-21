"""Compatibility shim for ibkr_trader.telemetry.

The canonical telemetry module now lives under ibkr_trader.core.telemetry.
Importing from this module is still supported for backwards compatibility.
"""

from ibkr_trader.core.telemetry import (
    EventBusTelemetrySink,
    FileTelemetrySink,
    LogTelemetrySink,
    TelemetryReporter,
    TelemetrySink,
    build_telemetry_reporter,
)

__all__ = [
    "TelemetrySink",
    "LogTelemetrySink",
    "EventBusTelemetrySink",
    "FileTelemetrySink",
    "TelemetryReporter",
    "build_telemetry_reporter",
]
