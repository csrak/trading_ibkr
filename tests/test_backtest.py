"""Integration tests for the backtest engine."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from ibkr_trader.backtest.engine import BacktestEngine
from ibkr_trader.events import EventBus
from ibkr_trader.portfolio import PortfolioState, RiskGuard
from ibkr_trader.sim.broker import SimulatedBroker, SimulatedMarketData
from ibkr_trader.strategy import (
    IndustryModelConfig,
    IndustryModelStrategy,
    SimpleMovingAverageStrategy,
    SMAConfig,
)


@pytest.mark.asyncio
async def test_backtest_engine_generates_executions() -> None:
    symbol = "AAPL"
    event_bus = EventBus()
    market_data = SimulatedMarketData(event_bus)
    portfolio = PortfolioState(Decimal("1000"))
    risk_guard = RiskGuard(portfolio=portfolio, max_exposure=Decimal("100000"))
    broker = SimulatedBroker(event_bus=event_bus, risk_guard=risk_guard)

    strategy_config = SMAConfig(symbols=[symbol], fast_period=2, slow_period=3, position_size=1)
    strategy = SimpleMovingAverageStrategy(
        config=strategy_config,
        broker=broker,
        event_bus=event_bus,
        risk_guard=risk_guard,
    )

    engine = BacktestEngine(
        symbol=symbol,
        event_bus=event_bus,
        market_data=market_data,
        broker=broker,
        portfolio=portfolio,
        risk_guard=risk_guard,
    )

    bars = [
        (datetime(2024, 1, 1, tzinfo=UTC), Decimal("100")),
        (datetime(2024, 1, 2, tzinfo=UTC), Decimal("101")),
        (datetime(2024, 1, 3, tzinfo=UTC), Decimal("102")),
        (datetime(2024, 1, 4, tzinfo=UTC), Decimal("101")),
        (datetime(2024, 1, 5, tzinfo=UTC), Decimal("100")),
        (datetime(2024, 1, 6, tzinfo=UTC), Decimal("99")),
        (datetime(2024, 1, 7, tzinfo=UTC), Decimal("98")),
    ]

    await engine.run(strategy, bars)

    assert broker.execution_events, "Expected simulated broker to record execution events"


@pytest.mark.asyncio
async def test_industry_model_strategy_uses_predictions(tmp_path: Path) -> None:
    symbol = "AAPL"
    event_bus = EventBus()
    market_data = SimulatedMarketData(event_bus)
    portfolio = PortfolioState(Decimal("1000"))
    risk_guard = RiskGuard(portfolio=portfolio, max_exposure=Decimal("100000"))
    broker = SimulatedBroker(event_bus=event_bus, risk_guard=risk_guard)

    artifact_path = tmp_path / "AAPL_linear_model.json"
    predictions_path = tmp_path / "AAPL_predictions.csv"

    predictions_path.write_text(
        "timestamp,predicted_price\n2024-01-01,105\n2024-01-02,104\n", encoding="utf-8"
    )
    artifact_path.write_text(
        json.dumps(
            {
                "target": symbol,
                "peers": ["MSFT", "GOOGL", "AMZN"],
                "horizon_days": 5,
                "intercept": 0.0,
                "coefficients": {"MSFT": 0.0, "GOOGL": 0.0, "AMZN": 0.0},
                "train_start": "2023-01-01",
                "train_end": "2023-12-31",
                "created_at": "2024-01-01T00:00:00",
                "prediction_path": predictions_path.name,
            }
        ),
        encoding="utf-8",
    )

    strategy_config = IndustryModelConfig(
        name="IndustryModel",
        symbols=[symbol],
        position_size=1,
        artifact_path=artifact_path,
        entry_threshold=Decimal("0.0"),
    )
    strategy = IndustryModelStrategy(
        config=strategy_config,
        broker=broker,
        event_bus=event_bus,
        risk_guard=risk_guard,
    )

    engine = BacktestEngine(
        symbol=symbol,
        event_bus=event_bus,
        market_data=market_data,
        broker=broker,
        portfolio=portfolio,
        risk_guard=risk_guard,
    )

    bars = [
        (datetime(2024, 1, 1, tzinfo=UTC), Decimal("100")),
        (datetime(2024, 1, 2, tzinfo=UTC), Decimal("101")),
    ]

    await engine.run(strategy, bars)

    assert broker.execution_events, "Industry model strategy should generate trades"
