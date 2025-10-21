"""Telemetry helpers for publishing diagnostic messages."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from loguru import logger

from ibkr_trader.core.events import DiagnosticEvent, EventBus, EventTopic


class TelemetrySink(Protocol):
    """Protocol implemented by telemetry sinks."""

    def emit(self, event: DiagnosticEvent) -> None | Awaitable[None]: ...


class LogTelemetrySink:
    """Emit telemetry entries to loguru logger."""

    def emit(self, event: DiagnosticEvent) -> None:
        logger.log(event.level.upper(), "[telemetry] {} {}", event.message, event.context or "")


class EventBusTelemetrySink:
    """Forward telemetry entries onto the in-process EventBus."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus

    async def emit(self, event: DiagnosticEvent) -> None:
        await self._event_bus.publish(EventTopic.DIAGNOSTIC, event)


class FileTelemetrySink:
    """Append telemetry entries to a JSON lines file."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: DiagnosticEvent) -> None:
        record = asdict(event)
        record["timestamp"] = event.timestamp.isoformat()
        record["context"] = self._sanitize(record.get("context"))
        with self._path.open("a", encoding="utf-8") as handle:
            json.dump(record, handle, separators=(",", ":"))
            handle.write("\n")

    def _sanitize(self, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, dict):
            return {str(k): self._sanitize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._sanitize(v) for v in value]
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Decimal):
            return float(value)
        return str(value)


class TelemetryReporter:
    """Emit telemetry messages to registered sinks."""

    def __init__(self, *sinks: TelemetrySink) -> None:
        if sinks:
            self._sinks: list[TelemetrySink] = list(sinks)
        else:
            self._sinks = [LogTelemetrySink()]

    def add_sink(self, sink: TelemetrySink) -> None:
        self._sinks.append(sink)

    def info(self, message: str, *, context: dict[str, object] | None = None) -> None:
        self._emit("INFO", message, context=context)

    def warning(self, message: str, *, context: dict[str, object] | None = None) -> None:
        self._emit("WARNING", message, context=context)

    def error(self, message: str, *, context: dict[str, object] | None = None) -> None:
        self._emit("ERROR", message, context=context)

    def _emit(self, level: str, message: str, *, context: dict[str, object] | None) -> None:
        event = DiagnosticEvent(
            level=level.upper(),
            message=message,
            timestamp=datetime.now(tz=UTC),
            context=context,
        )
        for sink in list(self._sinks):
            result = sink.emit(event)
            if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(result)  # pragma: no cover - unlikely in sync contexts
                else:
                    loop.create_task(result)  # pragma: no cover - scheduling


def build_telemetry_reporter(
    *,
    log_sink: bool = True,
    event_bus: EventBus | None = None,
    file_path: Path | None = None,
) -> TelemetryReporter:
    """Utility constructor assembling common telemetry sinks."""

    sinks: list[TelemetrySink] = []
    if log_sink:
        sinks.append(LogTelemetrySink())
    if file_path is not None:
        sinks.append(FileTelemetrySink(file_path))
    if event_bus is not None:
        sinks.append(EventBusTelemetrySink(event_bus))
    return TelemetryReporter(*sinks)
