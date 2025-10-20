from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ibkr_trader.base_strategy import BaseStrategy, BrokerProtocol
from ibkr_trader.events import EventBus, MarketDataEvent
from ibkr_trader.market_data import SubscriptionRequest
from ibkr_trader.models import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    SymbolContract,
)
from ibkr_trader.strategy import SimpleMovingAverageStrategy
from ibkr_trader.strategy_configs.config import StrategyConfig
from ibkr_trader.strategy_configs.graph import StrategyGraphConfig, StrategyNodeConfig
from ibkr_trader.strategy_coordinator.coordinator import (
    CoordinatorBrokerProxy,
    StrategyCoordinator,
)
from ibkr_trader.strategy_coordinator.errors import CapitalAllocationError
from ibkr_trader.strategy_coordinator.policies import EqualWeightPolicy


class DummyBroker(BrokerProtocol):
    async def place_order(self, request: OrderRequest) -> OrderResult:  # pragma: no cover
        raise NotImplementedError("place_order not used in coordinator tests")

    async def get_positions(self) -> list[Position]:
        return []


class DummyMarketDataService:
    def __init__(self) -> None:
        self.requests: list[SubscriptionRequest] = []

    @asynccontextmanager
    async def subscribe(self, request: SubscriptionRequest) -> AsyncIterator[None]:
        self.requests.append(request)
        yield


@pytest.mark.asyncio
async def test_coordinator_starts_and_subscribes(monkeypatch: pytest.MonkeyPatch) -> None:
    event_bus = EventBus()
    market_data = DummyMarketDataService()
    broker = DummyBroker()

    # Monkeypatch SMA strategy to avoid dependence on broker internals.
    started: list[str] = []
    stopped: list[str] = []

    original_start = SimpleMovingAverageStrategy.start
    original_stop = SimpleMovingAverageStrategy.stop

    async def fake_start(self: SimpleMovingAverageStrategy) -> None:  # type: ignore[no-untyped-def]
        started.append(self.config.name)

    async def fake_stop(self: SimpleMovingAverageStrategy) -> None:  # type: ignore[no-untyped-def]
        stopped.append(self.config.name)

    monkeypatch.setattr(SimpleMovingAverageStrategy, "start", fake_start)
    monkeypatch.setattr(SimpleMovingAverageStrategy, "stop", fake_stop)

    coordinator = StrategyCoordinator(
        broker=broker,
        event_bus=event_bus,
        market_data=market_data,  # type: ignore[arg-type]
        risk_guard=None,
    )

    graph = StrategyGraphConfig(
        strategies=[
            StrategyNodeConfig(
                id="sma1",
                type="sma",
                symbols=["AAPL", "MSFT"],
                params={"fast_period": 5, "slow_period": 20, "position_size": 10},
            )
        ]
    )

    try:
        await coordinator.start(graph)
        assert started, "Strategy start should have been invoked"
        assert {req.contract.symbol for req in market_data.requests} == {"AAPL", "MSFT"}
    finally:
        await coordinator.stop()
        monkeypatch.setattr(SimpleMovingAverageStrategy, "start", original_start)
        monkeypatch.setattr(SimpleMovingAverageStrategy, "stop", original_stop)

    assert stopped, "Strategy stop should have been called"


class CaptureBroker(BrokerProtocol):
    def __init__(self) -> None:
        self.requests: list[OrderRequest] = []

    async def place_order(self, request: OrderRequest) -> OrderResult:
        self.requests.append(request)
        return OrderResult(
            order_id=1,
            contract=request.contract,
            side=request.side,
            quantity=request.quantity,
            order_type=request.order_type,
            status=OrderStatus.SUBMITTED,
        )

    async def get_positions(self) -> list[Position]:
        return []


@pytest.mark.asyncio
async def test_broker_proxy_clips_quantity_and_notional() -> None:
    graph = StrategyGraphConfig(
        strategies=[
            StrategyNodeConfig(
                id="s1",
                type="sma",
                symbols=["AAPL"],
                max_position=5,
                max_notional=Decimal("1000"),
            )
        ]
    )

    policy = EqualWeightPolicy(graph.capital_policy)
    policy.prepare(graph)

    broker = CaptureBroker()
    proxy = CoordinatorBrokerProxy(
        strategy_id="s1",
        base_broker=broker,
        policy=policy,
        risk_guard=None,
        telemetry=None,
        exposure_hook=None,
    )

    request = OrderRequest(
        contract=SymbolContract(symbol="AAPL"),
        side=OrderSide.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        expected_price=Decimal("250"),
    )

    await proxy.place_order(request)

    assert broker.requests[0].quantity == 4  # clipped by notional (1000 / 250)


@pytest.mark.asyncio
async def test_broker_proxy_rejects_when_notional_allows_zero() -> None:
    graph = StrategyGraphConfig(
        strategies=[
            StrategyNodeConfig(
                id="s1",
                type="sma",
                symbols=["AAPL"],
                max_notional=Decimal("100"),
            )
        ]
    )

    policy = EqualWeightPolicy(graph.capital_policy)
    policy.prepare(graph)

    broker = CaptureBroker()
    proxy = CoordinatorBrokerProxy(
        strategy_id="s1",
        base_broker=broker,
        policy=policy,
        risk_guard=None,
        telemetry=None,
        exposure_hook=None,
    )

    request = OrderRequest(
        contract=SymbolContract(symbol="AAPL"),
        side=OrderSide.BUY,
        quantity=1,
        order_type=OrderType.MARKET,
        expected_price=Decimal("500"),
    )

    with pytest.raises(CapitalAllocationError):
        await proxy.place_order(request)


@pytest.mark.asyncio
async def test_target_position_intent_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    event_bus = EventBus()
    market_data = DummyMarketDataService()
    broker = CaptureBroker()

    coordinator = StrategyCoordinator(
        broker=broker,
        event_bus=event_bus,
        market_data=market_data,  # type: ignore[arg-type]
        risk_guard=None,
        telemetry=None,
        subscribe_market_data=False,
    )

    graph = StrategyGraphConfig(
        strategies=[
            StrategyNodeConfig(
                id="sma1",
                type="sma",
                symbols=["AAPL"],
                params={"fast_period": 5, "slow_period": 20, "position_size": 5},
                max_position=5,
            )
        ]
    )

    await coordinator.start(graph)

    try:
        wrapper = coordinator.strategies["sma1"]
        # Seed last event so price resolution succeeds
        wrapper.impl._last_event = MarketDataEvent(  # type: ignore[attr-defined]
            symbol="AAPL",
            price=Decimal("150"),
            timestamp=datetime.now(UTC),
        )
        wrapper.impl._last_prices["AAPL"] = Decimal("150")  # type: ignore[attr-defined]

        await wrapper.impl.submit_target_position("AAPL", 5)

        await asyncio.sleep(0.01)

        assert broker.requests, "Expected coordinator to forward order request"
        placed = broker.requests[0]
        assert placed.side == OrderSide.BUY
        assert placed.quantity == 5
        assert coordinator._total_notional > Decimal("0")  # type: ignore[attr-defined]
    finally:
        await coordinator.stop()


@pytest.mark.asyncio
async def test_factory_strategy_node_uses_config(monkeypatch: pytest.MonkeyPatch) -> None:
    event_bus = EventBus()
    market_data = DummyMarketDataService()
    broker = CaptureBroker()

    class DummyReplayStrategy(BaseStrategy):
        async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
            return None

    created_configs: list[str] = []

    def fake_create(config: StrategyConfig) -> DummyReplayStrategy:
        created_configs.append(config.strategy_type)
        return DummyReplayStrategy()

    monkeypatch.setattr("ibkr_trader.strategy_configs.factory.StrategyFactory.create", fake_create)

    coordinator = StrategyCoordinator(
        broker=broker,
        event_bus=event_bus,
        market_data=market_data,  # type: ignore[arg-type]
        risk_guard=None,
        telemetry=None,
        subscribe_market_data=False,
    )

    graph = StrategyGraphConfig(
        strategies=[
            StrategyNodeConfig(
                id="mean_rev_1",
                type="mean_reversion",
                symbols=["AAPL"],
                params={"execution": {"lookback_short": 10}},
            )
        ]
    )

    await coordinator.start(graph)
    try:
        assert created_configs == ["mean_reversion"]
        wrapper = coordinator.strategies["mean_rev_1"]
        assert wrapper.impl._intent_queue is not None  # type: ignore[attr-defined]
    finally:
        await coordinator.stop()
