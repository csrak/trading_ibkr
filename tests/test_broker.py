"""Unit tests for IBKRBroker ib_insync integration."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from eventkit import Event
from ib_insync import Contract

from ibkr_trader.broker import IBKRBroker
from ibkr_trader.config import IBKRConfig
from ibkr_trader.events import EventBus, EventTopic
from ibkr_trader.models import OrderRequest, OrderSide, OrderType, SymbolContract
from ibkr_trader.safety import LiveTradingGuard


def _make_ib_mock() -> MagicMock:
    ib_mock = MagicMock()
    ib_mock.connectAsync = AsyncMock(return_value=True)
    ib_mock.isConnected.return_value = True
    ib_mock.disconnect = MagicMock()
    ib_mock.qualifyContractsAsync = AsyncMock(return_value=[Contract()])
    ib_mock.placeOrder = MagicMock()
    ib_mock.reqPositionsAsync = AsyncMock(return_value=[])
    ib_mock.accountSummaryAsync = AsyncMock(return_value=[])
    ib_mock.whatIfOrderAsync = AsyncMock()
    ib_mock.sleep = AsyncMock(return_value=None)
    return ib_mock


def _trade_with_id(order_id: int = 1001) -> SimpleNamespace:
    loop = asyncio.get_event_loop()
    status_event = Event("status")
    trade = SimpleNamespace(
        order=SimpleNamespace(orderId=order_id),
        statusEvent=status_event,
        fillEvent=Event("fill"),
        commissionReportEvent=Event("commission"),
        orderStatus=SimpleNamespace(
            status="Submitted",
            filled=0,
            remaining=0,
            avgFillPrice=0.0,
        ),
    )
    loop.call_soon(status_event.emit, trade)
    return trade


@pytest.mark.asyncio
async def test_connect_marks_broker_connected() -> None:
    config = IBKRConfig()
    guard = LiveTradingGuard(config=config)
    ib_mock = _make_ib_mock()

    broker = IBKRBroker(config=config, guard=guard, ib_client=ib_mock)

    await broker.connect()

    ib_mock.connectAsync.assert_awaited_once()
    assert broker.is_connected


@pytest.mark.asyncio
async def test_place_order_qualifies_contract_and_waits_for_ack() -> None:
    config = IBKRConfig()
    guard = LiveTradingGuard(config=config)
    ib_mock = _make_ib_mock()
    trade = _trade_with_id(order_id=77)
    ib_mock.placeOrder.return_value = trade

    broker = IBKRBroker(config=config, guard=guard, ib_client=ib_mock)
    await broker.connect()

    order_request = OrderRequest(
        contract=SymbolContract(symbol="AAPL"),
        side=OrderSide.BUY,
        quantity=1,
        order_type=OrderType.MARKET,
    )

    result = await broker.place_order(order_request)

    ib_mock.qualifyContractsAsync.assert_awaited_once()
    ib_mock.placeOrder.assert_called_once()
    _, placed_order = ib_mock.placeOrder.call_args[0]
    assert placed_order.orderType == "MKT"
    assert result.order_id == 77


@pytest.mark.asyncio
async def test_stop_limit_order_sets_prices() -> None:
    config = IBKRConfig()
    guard = LiveTradingGuard(config=config)
    ib_mock = _make_ib_mock()
    trade = _trade_with_id(order_id=88)
    ib_mock.placeOrder.return_value = trade

    broker = IBKRBroker(config=config, guard=guard, ib_client=ib_mock)
    await broker.connect()

    order_request = OrderRequest(
        contract=SymbolContract(symbol="AAPL"),
        side=OrderSide.SELL,
        quantity=1,
        order_type=OrderType.STOP_LIMIT,
        limit_price=Decimal("150"),
        stop_price=Decimal("149"),
        time_in_force="GTC",
    )

    await broker.place_order(order_request)

    _, placed_order = ib_mock.placeOrder.call_args[0]
    assert placed_order.orderType == "STP LMT"
    assert placed_order.lmtPrice == 150.0
    assert placed_order.auxPrice == 149.0
    assert placed_order.tif == "GTC"


@pytest.mark.asyncio
async def test_preview_order_invokes_what_if() -> None:
    config = IBKRConfig()
    guard = LiveTradingGuard(config=config)
    ib_mock = _make_ib_mock()
    order_state = SimpleNamespace(
        initMarginChange="100.00",
        maintMarginChange="50.00",
        equityWithLoanChange="900.00",
        commission="1.00",
    )
    ib_mock.whatIfOrderAsync.return_value = order_state

    broker = IBKRBroker(config=config, guard=guard, ib_client=ib_mock)
    await broker.connect()

    order_request = OrderRequest(
        contract=SymbolContract(symbol="AAPL"),
        side=OrderSide.BUY,
        quantity=1,
        order_type=OrderType.MARKET,
    )

    state = await broker.preview_order(order_request)

    ib_mock.qualifyContractsAsync.assert_awaited()
    ib_mock.whatIfOrderAsync.assert_awaited_once()
    assert state.initMarginChange == "100.00"


@pytest.mark.asyncio
async def test_get_positions_uses_async_request() -> None:
    config = IBKRConfig()
    guard = LiveTradingGuard(config=config)
    ib_mock = _make_ib_mock()
    ib_mock.reqPositionsAsync.return_value = [
        SimpleNamespace(
            contract=SimpleNamespace(
                symbol="MSFT",
                secType="STK",
                exchange="SMART",
                currency="USD",
            ),
            position=5,
            avgCost=100.5,
            marketValue=502.5,
            unrealizedPNL=15.75,
        )
    ]

    broker = IBKRBroker(config=config, guard=guard, ib_client=ib_mock)
    await broker.connect()

    positions = await broker.get_positions()

    ib_mock.reqPositionsAsync.assert_awaited_once()
    assert positions[0].contract.symbol == "MSFT"
    assert positions[0].quantity == 5


@pytest.mark.asyncio
async def test_get_account_summary_returns_dict() -> None:
    config = IBKRConfig()
    guard = LiveTradingGuard(config=config)
    ib_mock = _make_ib_mock()
    ib_mock.accountSummaryAsync.return_value = [
        SimpleNamespace(tag="NetLiquidation", value="12345.67"),
        SimpleNamespace(tag="BuyingPower", value="9876.54"),
    ]

    broker = IBKRBroker(config=config, guard=guard, ib_client=ib_mock)
    await broker.connect()

    summary = await broker.get_account_summary()

    ib_mock.accountSummaryAsync.assert_awaited_once()
    assert summary["NetLiquidation"] == "12345.67"
    assert summary["BuyingPower"] == "9876.54"


@pytest.mark.asyncio
async def test_order_event_published_when_event_bus_provided() -> None:
    config = IBKRConfig()
    guard = LiveTradingGuard(config=config)
    ib_mock = _make_ib_mock()
    trade = _trade_with_id(order_id=55)
    trade.orderStatus.filled = 1
    ib_mock.placeOrder.return_value = trade

    event_bus = EventBus()
    subscription = event_bus.subscribe(EventTopic.ORDER_STATUS)

    broker = IBKRBroker(config=config, guard=guard, ib_client=ib_mock, event_bus=event_bus)
    await broker.connect()

    order_request = OrderRequest(
        contract=SymbolContract(symbol="AAPL"),
        side=OrderSide.BUY,
        quantity=1,
        order_type=OrderType.MARKET,
    )

    await broker.place_order(order_request)

    event = await asyncio.wait_for(subscription.get(), timeout=0.1)
    assert event.order_id == 55
    assert event.filled == 1
    assert event.status.value == "Submitted"
    assert event.side == OrderSide.BUY
    subscription.close()


@pytest.mark.asyncio
async def test_execution_events_emitted_on_fill() -> None:
    config = IBKRConfig()
    guard = LiveTradingGuard(config=config)
    ib_mock = _make_ib_mock()
    trade = _trade_with_id(order_id=90)
    ib_mock.placeOrder.return_value = trade

    event_bus = EventBus()
    subscription = event_bus.subscribe(EventTopic.EXECUTION)

    broker = IBKRBroker(
        config=config,
        guard=guard,
        ib_client=ib_mock,
        event_bus=event_bus,
    )
    await broker.connect()

    order_request = OrderRequest(
        contract=SymbolContract(symbol="AAPL"),
        side=OrderSide.BUY,
        quantity=1,
        order_type=OrderType.MARKET,
    )

    await broker.place_order(order_request)

    fill = SimpleNamespace(
        execution=SimpleNamespace(side="BOT", shares=1, price=120.0),
        commissionReport=None,
    )

    trade.fillEvent.emit(trade, fill)

    event = await asyncio.wait_for(subscription.get(), timeout=0.1)
    assert event.order_id == 90
    assert event.quantity == 1
    assert event.side == OrderSide.BUY
    assert event.price == Decimal("120.0")
    subscription.close()
