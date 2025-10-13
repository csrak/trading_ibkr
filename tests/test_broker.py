"""Unit tests for IBKRBroker ib_insync integration."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ib_insync import Contract

from ibkr_trader.broker import IBKRBroker
from ibkr_trader.config import IBKRConfig
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
    return ib_mock


def _trade_with_id(order_id: int = 1001) -> SimpleNamespace:
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    future.set_result(None)
    return SimpleNamespace(
        order=SimpleNamespace(orderId=order_id),
        orderStatusEvent=future,
    )


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
    assert result.order_id == 77


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
