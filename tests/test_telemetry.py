"""Tests for telemetry utilities."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from ibkr_trader.events import EventBus, EventTopic
from ibkr_trader.telemetry import (
    EventBusTelemetrySink,
    FileTelemetrySink,
    TelemetryReporter,
)


def test_file_telemetry_sink_writes_json(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    reporter = TelemetryReporter(FileTelemetrySink(path))

    reporter.warning("cache nearing ttl", context={"age": Decimal("12.5")})

    content = path.read_text(encoding="utf-8").strip()
    assert content, "Expected telemetry file to contain a record"
    record = json.loads(content)

    assert record["message"] == "cache nearing ttl"
    assert record["level"] == "WARNING"
    assert record["context"]["age"] == 12.5


@pytest.mark.asyncio
async def test_event_bus_sink_publishes_event() -> None:
    bus = EventBus()
    reporter = TelemetryReporter(EventBusTelemetrySink(bus))

    reporter.info("rate limit warning", context={"ratio": 0.9})

    subscription = bus.subscribe(EventTopic.DIAGNOSTIC)
    event = await asyncio.wait_for(subscription.get(), timeout=1.0)

    assert event.message == "rate limit warning"
    assert event.level == "INFO"
    assert event.context == {"ratio": 0.9}
