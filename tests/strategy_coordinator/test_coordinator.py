from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest

from ibkr_trader.base_strategy import BrokerProtocol
from ibkr_trader.events import EventBus
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
