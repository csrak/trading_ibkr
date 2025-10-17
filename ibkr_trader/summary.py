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
    recommended_actions: list[str]
    trade_stats: dict[str, str]
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
    actions = infer_actions(snapshot, warnings)
    trade_stats = extract_trade_stats(snapshot)
    return RunSummary(
        net_liquidation=net_liq,
        cash=cash,
        buying_power=buying_power,
        total_positions=total_positions,
        telemetry_warnings=warnings[-5:],
        recommended_actions=actions,
        trade_stats=trade_stats,
        raw_snapshot=snapshot,
    )


def extract_trade_stats(snapshot: dict[str, Any] | None) -> dict[str, str]:
    if snapshot is None:
        return {}
    stats = snapshot.get("trade_stats")
    if not isinstance(stats, dict):
        return {}
    per_symbol = snapshot.get("symbol_pnl")
    if isinstance(per_symbol, dict):
        stats = {**stats, "symbol_pnl": per_symbol}
    realized = snapshot.get("realized_pnl")
    if realized is not None:
        stats["realized_pnl"] = realized
    return {str(key): str(value) for key, value in stats.items()}


def infer_actions(snapshot: dict[str, Any] | None, warnings: list[str]) -> list[str]:
    actions: list[str] = []

    if warnings:
        if any("cache entry" in message.lower() for message in warnings):
            actions.append("Refresh caches before next run.")
        if any("rate limit" in message.lower() for message in warnings):
            actions.append("Reduce IBKR snapshots or increase interval.")
        if any("Option chain" in message for message in warnings):
            actions.append("Regenerate option chain cache.")

    if snapshot is None:
        actions.append("Run status command to capture new portfolio snapshot.")
        return actions

    positions = snapshot.get("positions")
    if isinstance(positions, dict) and len(positions) > 5:
        actions.append("Review high number of open positions.")

    net_liq = snapshot.get("net_liquidation")
    cash = snapshot.get("total_cash")
    if net_liq and cash:
        try:
            ratio = float(cash) / float(net_liq)
            if ratio < 0.1:
                actions.append("Cash below 10% of NetLiq; consider raising cash.")
        except (ValueError, ZeroDivisionError):
            pass

    symbol_pnl = snapshot.get("symbol_pnl")
    if isinstance(symbol_pnl, dict) and symbol_pnl:
        worst_symbol = min(symbol_pnl.items(), key=lambda item: float(item[1]))
        best_symbol = max(symbol_pnl.items(), key=lambda item: float(item[1]))
        actions.append(
            f"Review symbol performance: best={best_symbol[0]} ({best_symbol[1]}) "
            f"worst={worst_symbol[0]} ({worst_symbol[1]})"
        )

    return actions
