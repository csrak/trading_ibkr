"""Tests for dashboard telemetry handling."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from ibkr_trader.core.alerting import AlertMessage, AlertSeverity
from ibkr_trader.core.kill_switch import KillSwitch
from ibkr_trader.dashboard import TradingDashboard
from ibkr_trader.events import DiagnosticEvent, EventBus
from ibkr_trader.portfolio import PortfolioState


def test_dashboard_handles_screen_refresh_event(tmp_path: Path) -> None:
    event_bus = EventBus()
    portfolio = PortfolioState(max_daily_loss=Decimal("1000"))
    dashboard = TradingDashboard(
        event_bus=event_bus,
        portfolio=portfolio,
        max_position_size=100,
        max_daily_loss=Decimal("1000"),
        kill_switch=KillSwitch(tmp_path / "kill.json"),
    )

    generated_at = datetime.now(tz=UTC)
    event = DiagnosticEvent(
        level="INFO",
        message="adaptive_momentum.screen_refresh",
        timestamp=generated_at,
        context={
            "symbols": ["aapl", "msft"],
            "generated_at": generated_at.isoformat(),
        },
    )

    dashboard._handle_diagnostic_event(event)

    assert dashboard._latest_screener_symbols == ["AAPL", "MSFT"]
    assert dashboard._latest_screener_timestamp is not None


def test_dashboard_handles_alert_event(tmp_path: Path) -> None:
    event_bus = EventBus()
    portfolio = PortfolioState(max_daily_loss=Decimal("1000"))
    kill_switch = KillSwitch(tmp_path / "kill.json")
    dashboard = TradingDashboard(
        event_bus=event_bus,
        portfolio=portfolio,
        max_position_size=100,
        max_daily_loss=Decimal("1000"),
        kill_switch=kill_switch,
    )

    # ensure we start with non-engaged state
    assert not kill_switch.is_engaged()

    alert = AlertMessage(
        severity=AlertSeverity.WARNING,
        title="Trailing stop test",
        message="Test warning",
        timestamp=datetime.now(tz=UTC),
        context={},
    )

    dashboard._handle_alert_event(alert)

    assert dashboard._recent_alerts
    assert dashboard._recent_alerts[-1]["severity"] == "warning"
