"""Tests for OCO (One-Cancels-Other) order functionality."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ibkr_trader.events import EventBus
from ibkr_trader.models import (
    OCOOrderRequest,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    SymbolContract,
)
from ibkr_trader.oco_orders import OCOOrderManager, OCOPair

# Test OCOOrderRequest validation


def test_oco_order_request_requires_same_symbol() -> None:
    """Test that OCO orders must be for the same symbol."""
    with pytest.raises(ValueError, match="Both orders must be for same symbol"):
        OCOOrderRequest(
            order_a=OrderRequest(
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.BUY,
                quantity=10,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("145.00"),
            ),
            order_b=OrderRequest(
                contract=SymbolContract(symbol="GOOGL"),  # Different symbol
                side=OrderSide.SELL,
                quantity=10,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("155.00"),
            ),
            group_id="test_oco_1",
        )


def test_oco_order_request_requires_same_quantity() -> None:
    """Test that OCO orders must have the same quantity."""
    with pytest.raises(ValueError, match="Both orders must have same quantity"):
        OCOOrderRequest(
            order_a=OrderRequest(
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.BUY,
                quantity=10,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("145.00"),
            ),
            order_b=OrderRequest(
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.SELL,
                quantity=20,  # Different quantity
                order_type=OrderType.LIMIT,
                limit_price=Decimal("155.00"),
            ),
            group_id="test_oco_1",
        )


def test_oco_order_request_requires_nonempty_group_id() -> None:
    """Test that group_id cannot be empty."""
    with pytest.raises(ValueError, match="group_id cannot be empty"):
        OCOOrderRequest(
            order_a=OrderRequest(
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.BUY,
                quantity=10,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("145.00"),
            ),
            order_b=OrderRequest(
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.SELL,
                quantity=10,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("155.00"),
            ),
            group_id="",  # Empty group_id
        )


def test_oco_order_request_valid() -> None:
    """Test that valid OCO order request is accepted."""
    request = OCOOrderRequest(
        order_a=OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("145.00"),
        ),
        order_b=OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("155.00"),
        ),
        group_id="test_oco_1",
    )

    assert request.order_a.contract.symbol == "AAPL"
    assert request.order_b.contract.symbol == "AAPL"
    assert request.order_a.quantity == 10
    assert request.order_b.quantity == 10
    assert request.group_id == "test_oco_1"


# Test OCOPair serialization


def test_oco_pair_serialization() -> None:
    """Test OCO pair serialization and deserialization."""
    pair = OCOPair(
        group_id="test_oco_1",
        order_a_id=1001,
        order_b_id=1002,
        symbol="AAPL",
        quantity=10,
    )

    # Serialize
    data = pair.to_dict()
    assert data["group_id"] == "test_oco_1"
    assert data["order_a_id"] == 1001
    assert data["order_b_id"] == 1002
    assert data["symbol"] == "AAPL"
    assert data["quantity"] == 10

    # Deserialize
    pair2 = OCOPair.from_dict(data)
    assert pair2.group_id == pair.group_id
    assert pair2.order_a_id == pair.order_a_id
    assert pair2.order_b_id == pair.order_b_id
    assert pair2.symbol == pair.symbol
    assert pair2.quantity == pair.quantity


# Test OCOOrderManager


@pytest.mark.asyncio
async def test_oco_order_manager_place_order(tmp_path: Path) -> None:
    """Test placing an OCO order pair."""
    broker_mock = MagicMock()
    broker_mock.place_order = AsyncMock(
        side_effect=[
            OrderResult(
                order_id=1001,
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.BUY,
                quantity=10,
                order_type=OrderType.LIMIT,
                status=OrderStatus.SUBMITTED,
            ),
            OrderResult(
                order_id=1002,
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.SELL,
                quantity=10,
                order_type=OrderType.LIMIT,
                status=OrderStatus.SUBMITTED,
            ),
        ]
    )

    event_bus = EventBus()
    state_file = tmp_path / "oco_orders.json"

    manager = OCOOrderManager(broker=broker_mock, event_bus=event_bus, state_file=state_file)
    await manager.start()

    try:
        request = OCOOrderRequest(
            order_a=OrderRequest(
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.BUY,
                quantity=10,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("145.00"),
            ),
            order_b=OrderRequest(
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.SELL,
                quantity=10,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("155.00"),
            ),
            group_id="test_oco_1",
        )

        group_id = await manager.place_oco_order(request)

        assert group_id == "test_oco_1"
        assert group_id in manager.active_pairs
        assert broker_mock.place_order.call_count == 2

        oco_pair = manager.active_pairs[group_id]
        assert oco_pair.order_a_id == 1001
        assert oco_pair.order_b_id == 1002
        assert oco_pair.symbol == "AAPL"
        assert oco_pair.quantity == 10
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_oco_order_cancellation_on_fill(tmp_path: Path) -> None:
    """Test that one order is cancelled when the other fills."""
    broker_mock = MagicMock()
    broker_mock.place_order = AsyncMock(
        side_effect=[
            OrderResult(
                order_id=1001,
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.BUY,
                quantity=10,
                order_type=OrderType.LIMIT,
                status=OrderStatus.SUBMITTED,
            ),
            OrderResult(
                order_id=1002,
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.SELL,
                quantity=10,
                order_type=OrderType.LIMIT,
                status=OrderStatus.SUBMITTED,
            ),
        ]
    )

    event_bus = EventBus()
    state_file = tmp_path / "oco_orders.json"

    manager = OCOOrderManager(broker=broker_mock, event_bus=event_bus, state_file=state_file)
    await manager.start()

    try:
        request = OCOOrderRequest(
            order_a=OrderRequest(
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.BUY,
                quantity=10,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("145.00"),
            ),
            order_b=OrderRequest(
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.SELL,
                quantity=10,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("155.00"),
            ),
            group_id="test_oco_1",
        )

        group_id = await manager.place_oco_order(request)

        # Simulate order_a filling
        await manager._on_execution(1001, OrderStatus.FILLED)

        # OCO pair should be removed from active pairs
        assert group_id not in manager.active_pairs
        assert 1001 not in manager._order_to_group
        assert 1002 not in manager._order_to_group
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_oco_order_state_persistence(tmp_path: Path) -> None:
    """Test that OCO pair state is persisted and loaded."""
    broker_mock = MagicMock()
    broker_mock.place_order = AsyncMock(
        side_effect=[
            OrderResult(
                order_id=1001,
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.BUY,
                quantity=10,
                order_type=OrderType.LIMIT,
                status=OrderStatus.SUBMITTED,
            ),
            OrderResult(
                order_id=1002,
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.SELL,
                quantity=10,
                order_type=OrderType.LIMIT,
                status=OrderStatus.SUBMITTED,
            ),
        ]
    )

    event_bus = EventBus()
    state_file = tmp_path / "oco_orders.json"

    # Create manager and place order
    manager = OCOOrderManager(broker=broker_mock, event_bus=event_bus, state_file=state_file)
    await manager.start()

    request = OCOOrderRequest(
        order_a=OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("145.00"),
        ),
        order_b=OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("155.00"),
        ),
        group_id="test_oco_1",
    )

    group_id = await manager.place_oco_order(request)
    await manager.stop()

    # Create new manager and verify state was loaded
    manager2 = OCOOrderManager(broker=broker_mock, event_bus=event_bus, state_file=state_file)

    assert group_id in manager2.active_pairs
    oco_pair = manager2.active_pairs[group_id]
    assert oco_pair.order_a_id == 1001
    assert oco_pair.order_b_id == 1002
    assert oco_pair.symbol == "AAPL"
    assert oco_pair.quantity == 10


@pytest.mark.asyncio
async def test_oco_order_ignores_unrelated_executions(tmp_path: Path) -> None:
    """Test that unrelated order executions are ignored."""
    broker_mock = MagicMock()
    broker_mock.place_order = AsyncMock(
        side_effect=[
            OrderResult(
                order_id=1001,
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.BUY,
                quantity=10,
                order_type=OrderType.LIMIT,
                status=OrderStatus.SUBMITTED,
            ),
            OrderResult(
                order_id=1002,
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.SELL,
                quantity=10,
                order_type=OrderType.LIMIT,
                status=OrderStatus.SUBMITTED,
            ),
        ]
    )

    event_bus = EventBus()
    state_file = tmp_path / "oco_orders.json"

    manager = OCOOrderManager(broker=broker_mock, event_bus=event_bus, state_file=state_file)
    await manager.start()

    try:
        request = OCOOrderRequest(
            order_a=OrderRequest(
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.BUY,
                quantity=10,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("145.00"),
            ),
            order_b=OrderRequest(
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.SELL,
                quantity=10,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("155.00"),
            ),
            group_id="test_oco_1",
        )

        group_id = await manager.place_oco_order(request)

        # Simulate execution of unrelated order
        await manager._on_execution(9999, OrderStatus.FILLED)

        # OCO pair should still be active
        assert group_id in manager.active_pairs
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_oco_order_handles_already_cancelled(tmp_path: Path) -> None:
    """Test that already cancelled OCO pairs are not processed again."""
    broker_mock = MagicMock()
    broker_mock.place_order = AsyncMock(
        side_effect=[
            OrderResult(
                order_id=1001,
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.BUY,
                quantity=10,
                order_type=OrderType.LIMIT,
                status=OrderStatus.SUBMITTED,
            ),
            OrderResult(
                order_id=1002,
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.SELL,
                quantity=10,
                order_type=OrderType.LIMIT,
                status=OrderStatus.SUBMITTED,
            ),
        ]
    )

    event_bus = EventBus()
    state_file = tmp_path / "oco_orders.json"

    manager = OCOOrderManager(broker=broker_mock, event_bus=event_bus, state_file=state_file)
    await manager.start()

    try:
        request = OCOOrderRequest(
            order_a=OrderRequest(
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.BUY,
                quantity=10,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("145.00"),
            ),
            order_b=OrderRequest(
                contract=SymbolContract(symbol="AAPL"),
                side=OrderSide.SELL,
                quantity=10,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("155.00"),
            ),
            group_id="test_oco_1",
        )

        group_id = await manager.place_oco_order(request)

        # First fill
        await manager._on_execution(1001, OrderStatus.FILLED)

        # OCO pair should be removed
        assert group_id not in manager.active_pairs

        # Second fill (should be ignored since pair already cancelled)
        await manager._on_execution(1002, OrderStatus.FILLED)

        # No errors should occur
    finally:
        await manager.stop()
