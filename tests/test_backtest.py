"""Integration tests for the backtest engine."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ibkr_trader.backtest.engine import BacktestEngine
from ibkr_trader.events import EventBus
from ibkr_trader.portfolio import PortfolioState, RiskGuard
from ibkr_trader.sim.broker import SimulatedBroker, SimulatedMarketData
from ibkr_trader.strategy import SimpleMovingAverageStrategy, SMAConfig


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
