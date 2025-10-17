"""Helpers for producing end-of-run summaries for traders."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunSummary:
    """Structured summary of a trading session/backtest."""

    net_liquidation: str | None
    cash: str | None
    buying_power: str | None
    total_positions: int
    telemetry_warnings: list[str]
    raw_snapshot: dict[str, Any] | None

    def headline(self) -> str:
        return (
            f"NetLiq={self.net_liquidation or 'n/a'} | "
            f"Cash={self.cash or 'n/a'} | "
            f"Positions={self.total_positions}"
        )


def load_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - defensive
        return None


def summarize_portfolio(
    snapshot: dict[str, Any] | None,
) -> tuple[str | None, str | None, str | None, int]:
    if snapshot is None:
        return None, None, None, 0
    net_liq = snapshot.get("net_liquidation")
    cash = snapshot.get("total_cash")
    buying_power = snapshot.get("buying_power")
    positions = snapshot.get("positions")
    total_positions = len(positions) if isinstance(positions, dict) else 0
    return (
        str(net_liq) if net_liq is not None else None,
        str(cash) if cash is not None else None,
        str(buying_power) if buying_power is not None else None,
        total_positions,
    )


def summarize_run(snapshot_path: Path, telemetry_lines: list[str]) -> RunSummary:
    snapshot = load_snapshot(snapshot_path)
    net_liq, cash, buying_power, total_positions = summarize_portfolio(snapshot)
    warnings = [line for line in telemetry_lines if "WARNING" in line or "ERROR" in line]
    return RunSummary(
        net_liquidation=net_liq,
        cash=cash,
        buying_power=buying_power,
        total_positions=total_positions,
        telemetry_warnings=warnings[-5:],
        raw_snapshot=snapshot,
    )
