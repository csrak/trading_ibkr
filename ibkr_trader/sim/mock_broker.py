"""Mock broker for simulation that uses event bus to publish fills."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from ibkr_trader.events import EventBus, EventTopic, ExecutionEvent, OrderStatusEvent
from ibkr_trader.models import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    SymbolContract,
)
from model.data.models import OrderStateSnapshot
from model.data.models import OrderStatus as MMOrderStatus


class MockBroker:
    """Simplified broker that simulates order acknowledgments and fills."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._next_order_id = 1
        self._orders: dict[int, OrderStateSnapshot] = {}
        self._order_meta: dict[int, tuple[SymbolContract, OrderSide]] = {}
        self._positions: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def submit_limit_order(self, request: OrderRequest) -> OrderResult:
        if request.order_type != OrderType.LIMIT:
            raise ValueError("MockBroker currently supports only LIMIT orders")

        async with self._lock:
            order_id = self._next_order_id
            self._next_order_id += 1

            state = OrderStateSnapshot(
                order_id=str(order_id),
                status=MMOrderStatus.WORKING,
                submitted_at=datetime.now(UTC),
                filled_qty=0.0,
                remaining_qty=float(request.quantity),
                venue="SIM",
            )
            self._orders[order_id] = state
            self._order_meta[order_id] = (request.contract, request.side)

        await self._event_bus.publish(
            EventTopic.ORDER_STATUS,
            OrderStatusEvent(
                order_id=order_id,
                status=OrderStatus.SUBMITTED,
                contract=request.contract,
                side=request.side,
                filled=0,
                remaining=request.quantity,
                avg_fill_price=0.0,
                timestamp=datetime.now(UTC),
            ),
        )

        return OrderResult(
            order_id=order_id,
            contract=request.contract,
            side=request.side,
            quantity=request.quantity,
            order_type=request.order_type,
            status=OrderStatus.SUBMITTED,
        )

    async def cancel_order(self, order_id: int) -> None:
        async with self._lock:
            state = self._orders.get(order_id)
            meta = self._order_meta.get(order_id)
            if state is None or meta is None:
                return
            state.status = MMOrderStatus.CANCELLED
            state.updated_at = datetime.now(UTC)

        await self._event_bus.publish(
            EventTopic.ORDER_STATUS,
            OrderStatusEvent(
                order_id=order_id,
                status=OrderStatus.CANCELLED,
                contract=meta[0],
                side=meta[1],
                filled=int(state.filled_qty),
                remaining=int(state.remaining_qty if state.remaining_qty is not None else 0),
                avg_fill_price=float(state.avg_price or 0.0),
                timestamp=datetime.now(UTC),
            ),
        )

    async def simulate_fill(
        self,
        order_id: int,
        fill_quantity: int,
        fill_price: Decimal,
    ) -> None:
        async with self._lock:
            state = self._orders.get(order_id)
            meta = self._order_meta.get(order_id)
            if state is None or meta is None:
                return
            state.filled_qty += fill_quantity
            remaining = max(
                0.0, (state.remaining_qty if state.remaining_qty is not None else 0.0) - fill_quantity
            )
            state.remaining_qty = remaining
            state.avg_price = float(fill_price)
            state.updated_at = datetime.now(UTC)
            if remaining <= 0:
                state.status = MMOrderStatus.FILLED
            contract, side = meta

        await self._event_bus.publish(
            EventTopic.ORDER_STATUS,
            OrderStatusEvent(
                order_id=order_id,
                status=OrderStatus.FILLED if remaining <= 0 else OrderStatus.SUBMITTED,
                contract=contract,
                side=side,
                filled=int(state.filled_qty),
                remaining=int(remaining),
                avg_fill_price=float(fill_price),
                timestamp=datetime.now(UTC),
            ),
        )

        await self._event_bus.publish(
            EventTopic.EXECUTION,
            ExecutionEvent(
                order_id=order_id,
                contract=contract,
                side=side,
                quantity=fill_quantity,
                price=fill_price,
                commission=Decimal("0"),
                timestamp=datetime.now(UTC),
            ),
        )

        # Update position tracking
        delta = fill_quantity if side == OrderSide.BUY else -fill_quantity
        symbol = contract.symbol
        self._positions[symbol] = self._positions.get(symbol, 0) + delta
        if self._positions[symbol] == 0:
            self._positions.pop(symbol, None)

    async def place_order(self, request: OrderRequest) -> OrderResult:
        """Place an order (BrokerProtocol compliance).

        For now, delegates to submit_limit_order for LIMIT orders.
        Other order types will raise ValueError.
        """
        return await self.submit_limit_order(request)

    async def get_positions(self) -> list[Position]:
        """Get current positions (BrokerProtocol compliance).

        Returns list of positions tracked internally from fills.
        """
        return [
            Position(
                contract=SymbolContract(symbol=symbol),
                quantity=quantity,
                avg_cost=Decimal("0"),
                market_value=Decimal("0"),
                unrealized_pnl=Decimal("0"),
            )
            for symbol, quantity in self._positions.items()
            if quantity != 0
        ]
