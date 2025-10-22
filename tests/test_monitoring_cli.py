"""Tests for monitoring CLI helper commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ibkr_trader.cli import app as root_app
from ibkr_trader.cli_commands.monitoring import monitoring_app


def test_alert_history_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()

    history = tmp_path / "alerts_history.jsonl"
    history.write_text(
        '{"severity":"INFO","title":"Test Alert","message":"Example",'
        '"timestamp":"2025-01-01T00:00:00+00:00","context":{"source":"synthetic_alerts","session_id":"test-session"}}\n'
        '{"severity":"WARNING","title":"Warning Alert","message":"Warn",'
        '"timestamp":"2025-01-01T00:05:00+00:00","context":{"source":"live","session_id":"test-session"}}\n'
    )

    class DummyConfig:
        log_dir = tmp_path

    monkeypatch.setattr(
        "ibkr_trader.cli_commands.monitoring.load_config",
        lambda: DummyConfig(),
    )

    result = runner.invoke(monitoring_app, ["alert-history", "--limit", "5"])
    assert result.exit_code == 0
    assert "Test Alert" in result.stdout

    # Ensure the command is available through the root CLI namespace
    result_root = runner.invoke(root_app, ["monitoring", "alert-history", "--limit", "5"])
    assert result_root.exit_code == 0
    assert "Test Alert" in result_root.stdout

    # Filter by severity and session id
    filtered = runner.invoke(
        monitoring_app,
        [
            "alert-history",
            "--severity",
            "WARNING",
            "--session-id",
            "test-session",
            "--limit",
            "5",
        ],
    )
    assert filtered.exit_code == 0
    assert "Warning Alert" in filtered.stdout
    assert "Test Alert" not in filtered.stdout

    # Source filter should match only synthetic entry (and JSON output)
    source_filtered = runner.invoke(
        monitoring_app,
        ["alert-history", "--source", "synthetic_alerts", "--limit", "5", "--json"],
    )
    assert source_filtered.exit_code == 0
    assert "synthetic_alerts" in source_filtered.stdout
    assert "Warning Alert" not in source_filtered.stdout
