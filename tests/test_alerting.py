"""Tests for telemetry alert routing."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ibkr_trader.core.alerting import (
    AlertMessage,
    AlertSeverity,
    AlertTransport,
    TelemetryAlertConfig,
    TelemetryAlertRouter,
)
from ibkr_trader.core.events import DiagnosticEvent, EventBus, EventTopic
from ibkr_trader.core.kill_switch import KillSwitch


class CollectingAlertTransport(AlertTransport):
    """In-memory transport for assertions."""

    def __init__(self) -> None:
        self.alerts: list[AlertMessage] = []

    def send(self, alert: AlertMessage) -> None:
        self.alerts.append(alert)


@pytest.mark.asyncio
async def test_alert_router_trailing_stop_threshold_triggers_alert(tmp_path: Path) -> None:
    event_bus = EventBus()
    transport = CollectingAlertTransport()
    history = tmp_path / "alerts.jsonl"
    router = TelemetryAlertRouter(
        event_bus=event_bus,
        transport=transport,
        config=TelemetryAlertConfig(
            trailing_rate_limit_threshold=3,
            trailing_rate_limit_window=timedelta(seconds=30),
            trailing_rate_limit_cooldown=timedelta(seconds=10),
            screener_stale_after=timedelta(seconds=60),
            screener_check_interval=timedelta(seconds=30),
        ),
        history_path=history,
    )

    alert_sub = event_bus.subscribe(EventTopic.ALERT)

    await router.start()
    base_time = datetime.now(tz=UTC)
    for idx in range(3):
        event = DiagnosticEvent(
            level="WARNING",
            message="trailing_stop.rate_limited",
            timestamp=base_time + timedelta(seconds=idx),
            context={"stop_id": "ABC_1", "symbol": "ABC"},
        )
        await event_bus.publish(EventTopic.DIAGNOSTIC, event)
        await asyncio.sleep(0)  # allow router to process

    alert_event = await asyncio.wait_for(alert_sub.get(), timeout=1)
    await asyncio.sleep(0.05)
    await router.stop()
    alert_sub.close()

    assert transport.alerts, "expected an alert for rate limit threshold"
    alert = transport.alerts[0]
    assert alert.severity == AlertSeverity.WARNING
    assert "trailing stop" in alert.title.lower()
    assert alert_event.severity == AlertSeverity.WARNING
    assert alert_event.message


@pytest.mark.asyncio
async def test_alert_router_screener_staleness_alerts_and_recovers(tmp_path: Path) -> None:
    event_bus = EventBus()
    transport = CollectingAlertTransport()
    history = tmp_path / "alerts.jsonl"
    router = TelemetryAlertRouter(
        event_bus=event_bus,
        transport=transport,
        config=TelemetryAlertConfig(
            trailing_rate_limit_threshold=10,
            trailing_rate_limit_window=timedelta(seconds=60),
            trailing_rate_limit_cooldown=timedelta(seconds=60),
            screener_stale_after=timedelta(seconds=1),
            screener_check_interval=timedelta(milliseconds=100),
        ),
        history_path=history,
    )

    alert_sub = event_bus.subscribe(EventTopic.ALERT)

    await router.start()
    refresh_event = DiagnosticEvent(
        level="INFO",
        message="adaptive_momentum.screen_refresh",
        timestamp=datetime.now(tz=UTC),
        context={"symbols": ["AAPL"], "generated_at": datetime.now(tz=UTC).isoformat()},
    )
    await event_bus.publish(EventTopic.DIAGNOSTIC, refresh_event)
    await asyncio.sleep(0.2)

    # Wait for staleness alert
    await asyncio.sleep(1.2)
    assert any(alert.severity == AlertSeverity.CRITICAL for alert in transport.alerts)
    critical_event = await asyncio.wait_for(alert_sub.get(), timeout=1)

    # Emit another refresh to generate recovery info alert
    recovery_event = DiagnosticEvent(
        level="INFO",
        message="adaptive_momentum.screen_refresh",
        timestamp=datetime.now(tz=UTC),
        context={"symbols": ["AAPL"], "generated_at": datetime.now(tz=UTC).isoformat()},
    )
    await event_bus.publish(EventTopic.DIAGNOSTIC, recovery_event)
    await asyncio.sleep(0.2)

    await router.stop()
    alert_sub.close()
    assert any(alert.severity == AlertSeverity.INFO for alert in transport.alerts)
    assert critical_event.severity == AlertSeverity.CRITICAL


@pytest.mark.asyncio
async def test_alert_router_engages_kill_switch(tmp_path: Path) -> None:
    event_bus = EventBus()
    transport = CollectingAlertTransport()
    kill_switch = KillSwitch(tmp_path / "kill.json")
    triggered = asyncio.Event()

    def _on_kill(_: AlertMessage) -> None:
        triggered.set()

    history = tmp_path / "alerts.jsonl"
    router = TelemetryAlertRouter(
        event_bus=event_bus,
        transport=transport,
        kill_switch=kill_switch,
        on_kill=_on_kill,
        extra_context={"session_id": "test_session"},
        history_path=history,
    )

    alert = AlertMessage(
        severity=AlertSeverity.CRITICAL,
        title="Critical test",
        message="Test critical alert",
        timestamp=datetime.now(tz=UTC),
        context={},
    )

    router._dispatch(alert)
    await asyncio.sleep(0)

    assert kill_switch.is_engaged()
    assert triggered.is_set()
    assert kill_switch.status().context.get("session_id") == "test_session"
    assert history.exists()

    assert kill_switch.clear(acknowledged_by="tester", note="checked")
    cleared_state = kill_switch.status()
    assert not cleared_state.engaged
    assert cleared_state.acknowledged
