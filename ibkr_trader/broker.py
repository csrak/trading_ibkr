"""IBKR broker connection and order management."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ib_insync import (
    IB,
    CommissionReport,
    Contract,
    Fill,
    LimitOrder,
    MarketOrder,
    Order,
    OrderState,
    StopOrder,
    Trade,
)
from loguru import logger

from ibkr_trader.config import IBKRConfig
from ibkr_trader.events import EventBus, EventTopic, ExecutionEvent, OrderStatusEvent
from ibkr_trader.models import (
    BracketOrderRequest,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    SymbolContract,
)
from ibkr_trader.safety import LiveTradingGuard

if TYPE_CHECKING:
    from ibkr_trader.portfolio import RiskGuard


class IBKRBroker:
    """IBKR broker connection and trading interface.

    Handles connection to TWS/Gateway and order execution with safety guards.
    """

    def __init__(
        self,
        config: IBKRConfig,
        guard: LiveTradingGuard,
        ib_client: IB | None = None,
        event_bus: EventBus | None = None,
        risk_guard: RiskGuard | None = None,
    ) -> None:
        """Initialize broker connection.

        Args:
            config: IBKR configuration
            guard: Trading safety guard
            ib_client: Optional injected IB client (primarily for testing)
            event_bus: Optional event bus for publishing broker events
        """
        self.config = config
        self.guard = guard
        self.ib = ib_client or IB()
        self._connected = False
        self._event_bus = event_bus
        self._risk_guard = risk_guard

    async def connect(self, timeout: float = 10.0) -> None:
        """Connect to IBKR TWS/Gateway."""
        if self._connected:
            logger.warning("Already connected to IBKR")
            return

        logger.info(
            f"Connecting to IBKR at {self.config.host}:{self.config.port} "
            f"(client_id={self.config.client_id})"
        )

        try:
            is_connected = await asyncio.wait_for(
                self.ib.connectAsync(
                    host=self.config.host,
                    port=self.config.port,
                    clientId=self.config.client_id,
                ),
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise ConnectionError(
                f"Timed out connecting to IBKR at {self.config.host}:{self.config.port}"
            ) from exc

        if not is_connected or not self.ib.isConnected():
            raise ConnectionError("Failed to establish IBKR connection")

        self._connected = True
        logger.info(f"Connected to IBKR - Mode: {self.config.trading_mode.value}")

    async def disconnect(self) -> None:
        """Disconnect from IBKR."""
        if self._connected:
            self.ib.disconnect()
            self._connected = False
            logger.info("Disconnected from IBKR")

    def _ensure_connected(self) -> None:
        """Verify that the IB client is connected before making requests."""
        if not self._connected or not self.ib.isConnected():
            raise RuntimeError("IBKR connection is not active. Call connect() first.")

    @property
    def is_connected(self) -> bool:
        """Return True when the underlying IB client reports an active connection."""
        return self._connected and self.ib.isConnected()

    def _create_contract(self, symbol_contract: SymbolContract) -> Contract:
        """Create IB contract from symbol contract.

        Args:
            symbol_contract: Symbol contract definition

        Returns:
            IB Contract object
        """
        contract = Contract()
        contract.symbol = symbol_contract.symbol
        contract.secType = symbol_contract.sec_type
        contract.exchange = symbol_contract.exchange
        contract.currency = symbol_contract.currency
        return contract

    def _create_order(self, order_request: OrderRequest) -> Order:
        """Create IB order from order request.

        Args:
            order_request: Order request

        Returns:
            IB Order object
        """
        action = "BUY" if order_request.side == OrderSide.BUY else "SELL"
        quantity = order_request.quantity

        if order_request.order_type == OrderType.MARKET:
            order = MarketOrder(action, quantity)
        elif order_request.order_type == OrderType.LIMIT:
            if order_request.limit_price is None:
                raise ValueError("Limit price required for limit orders")
            order = LimitOrder(action, quantity, float(order_request.limit_price))
        elif order_request.order_type == OrderType.STOP:
            if order_request.stop_price is None:
                raise ValueError("Stop price required for stop orders")
            order = StopOrder(action, quantity, float(order_request.stop_price))
        elif order_request.order_type == OrderType.STOP_LIMIT:
            if order_request.stop_price is None or order_request.limit_price is None:
                raise ValueError("Stop limit orders require both stop and limit prices")
            order = Order()
            order.action = action
            order.orderType = "STP LMT"
            order.totalQuantity = quantity
            order.auxPrice = float(order_request.stop_price)
            order.lmtPrice = float(order_request.limit_price)
        else:
            raise ValueError(f"Unsupported order type: {order_request.order_type}")

        if order_request.time_in_force:
            order.tif = order_request.time_in_force
        order.transmit = order_request.transmit
        return order

    async def preview_order(self, order_request: OrderRequest) -> OrderState:
        """Request IBKR what-if (margin) preview for the provided order."""
        # Safety check still applies in preview mode
        self.guard.check_order_safety(
            symbol=order_request.contract.symbol,
            quantity=order_request.quantity,
        )

        self._ensure_connected()

        base_contract = self._create_contract(order_request.contract)
        qualified_contracts = await self.ib.qualifyContractsAsync(base_contract)
        if not qualified_contracts:
            raise ValueError(
                f"Unable to qualify contract for symbol {order_request.contract.symbol}"
            )

        contract = qualified_contracts[0]
        order = self._create_order(order_request)
        order.whatIf = True

        order_state = await self.ib.whatIfOrderAsync(contract, order)

        logger.info(
            "Preview received: initMarginChange={init}, maintMarginChange={maint}, "
            "equityWithLoanChange={eq}",
            init=order_state.initMarginChange,
            maint=order_state.maintMarginChange,
            eq=order_state.equityWithLoanChange,
        )

        return order_state

    async def place_order(self, order_request: OrderRequest) -> OrderResult:
        """Place an order with safety checks.

        Args:
            order_request: Order to place

        Returns:
            Order execution result

        Raises:
            LiveTradingError: If safety checks fail
        """
        # Safety check
        self.guard.check_order_safety(
            symbol=order_request.contract.symbol, quantity=order_request.quantity
        )

        if self._risk_guard is not None:
            price_for_risk = (
                order_request.expected_price
                or order_request.limit_price
                or order_request.stop_price
                or Decimal("0")
            )
            await self._risk_guard.validate_order(
                contract=order_request.contract,
                side=order_request.side,
                quantity=order_request.quantity,
                price=Decimal(price_for_risk),
            )

        self._ensure_connected()

        # Log order details
        logger.info(
            f"Placing order: {order_request.side.value} {order_request.quantity} "
            f"{order_request.contract.symbol} @ {order_request.order_type.value}"
        )
        if order_request.limit_price is not None:
            logger.debug(f"  limit_price={order_request.limit_price}")
        if order_request.stop_price is not None:
            logger.debug(f"  stop_price={order_request.stop_price}")
        if order_request.time_in_force:
            logger.debug(f"  time_in_force={order_request.time_in_force}")

        # Create IB objects
        base_contract = self._create_contract(order_request.contract)
        qualified_contracts = await self.ib.qualifyContractsAsync(base_contract)
        if not qualified_contracts:
            raise ValueError(
                f"Unable to qualify contract for symbol {order_request.contract.symbol}"
            )

        contract = qualified_contracts[0]
        order = self._create_order(order_request)

        # Place order
        trade = self.ib.placeOrder(contract, order)
        loop = asyncio.get_running_loop()

        def _handle_fill(trade_obj: Trade, fill: Fill) -> None:  # pragma: no cover - callback
            try:
                exec_side = (
                    OrderSide.BUY
                    if fill.execution.side.upper() in {"BOT", "BUY"}
                    else OrderSide.SELL
                )
                quantity = int(fill.execution.shares)
                fill_price = Decimal(str(fill.execution.price))
                commission_value = Decimal("0")
                if fill.commissionReport is not None:
                    commission_value = Decimal(str(fill.commissionReport.commission))

                event = ExecutionEvent(
                    order_id=trade_obj.order.orderId,
                    contract=order_request.contract,
                    side=exec_side,
                    quantity=quantity,
                    price=fill_price,
                    commission=commission_value,
                    timestamp=datetime.now(UTC),
                )
                loop.create_task(self._publish_execution(event))
            except Exception as exc:
                logger.warning("Failed to handle execution event: %s", exc)

        def _handle_commission(
            trade_obj: Trade, fill: Fill, report: CommissionReport
        ) -> None:  # pragma: no cover - callback
            try:
                exec_side = (
                    OrderSide.BUY
                    if fill.execution.side.upper() in {"BOT", "BUY"}
                    else OrderSide.SELL
                )
                quantity = int(fill.execution.shares)
                fill_price = Decimal(str(fill.execution.price))
                commission_value = Decimal(str(report.commission))
                event = ExecutionEvent(
                    order_id=trade_obj.order.orderId,
                    contract=order_request.contract,
                    side=exec_side,
                    quantity=quantity,
                    price=fill_price,
                    commission=commission_value,
                    timestamp=datetime.now(UTC),
                )
                loop.create_task(self._publish_execution(event))
            except Exception as exc:
                logger.warning("Failed to handle commission report: %s", exc)

        trade.fillEvent += _handle_fill
        trade.commissionReportEvent += _handle_commission

        status_event = getattr(trade, "statusEvent", None)

        try:
            if status_event is not None:
                await asyncio.wait_for(status_event, timeout=5)
            else:
                # Fall back to short sleep if trade object lacks status events
                await self.ib.sleep(1)
        except TimeoutError:
            logger.warning(
                "Timed out waiting for order acknowledgement from IBKR "
                f"(order_id={trade.order.orderId})"
            )

        ib_status = trade.orderStatus
        result_status = self._map_order_status(ib_status.status)
        filled_quantity = int(getattr(ib_status, "filled", 0) or 0)
        remaining_quantity = int(getattr(ib_status, "remaining", 0) or 0)
        avg_fill_price = float(getattr(ib_status, "avgFillPrice", 0.0) or 0.0)

        # Create result
        result = OrderResult(
            order_id=trade.order.orderId,
            contract=order_request.contract,
            side=order_request.side,
            quantity=order_request.quantity,
            order_type=order_request.order_type,
            status=result_status,
            filled_quantity=filled_quantity,
            avg_fill_price=Decimal(str(avg_fill_price)),
        )

        logger.info(f"Order submitted with ID: {result.order_id}")

        await self._publish_order_status(
            OrderStatusEvent(
                order_id=result.order_id,
                status=result_status,
                contract=order_request.contract,
                side=order_request.side,
                filled=filled_quantity,
                remaining=remaining_quantity,
                avg_fill_price=avg_fill_price,
                timestamp=datetime.now(UTC),
            )
        )

        return result

    async def place_bracket_order(self, bracket_request: BracketOrderRequest) -> OrderResult:
        """Place a bracket order (entry + stop loss + take profit).

        A bracket order consists of three linked orders:
        1. Parent order (entry) - executed first
        2. Stop loss order - activated when parent fills
        3. Take profit order - activated when parent fills

        When either child order fills, the other is automatically cancelled (OCO).

        Args:
            bracket_request: Bracket order specification

        Returns:
            OrderResult for the parent order (includes child_order_ids)

        Raises:
            LiveTradingError: If safety checks fail
            ValueError: If bracket order validation fails
        """
        # Validate bracket order structure
        parent_req = bracket_request.parent
        stop_req = bracket_request.stop_loss
        take_profit_req = bracket_request.take_profit

        # Safety check on parent order
        self.guard.check_order_safety(
            symbol=parent_req.contract.symbol,
            quantity=parent_req.quantity,
        )

        if self._risk_guard is not None:
            price_for_risk = (
                parent_req.expected_price
                or parent_req.limit_price
                or parent_req.stop_price
                or Decimal("0")
            )
            await self._risk_guard.validate_order(
                contract=parent_req.contract,
                side=parent_req.side,
                quantity=parent_req.quantity,
                price=Decimal(price_for_risk),
            )

        self._ensure_connected()

        # Log bracket order details
        logger.info(
            f"Placing bracket order: {parent_req.side.value} {parent_req.quantity} "
            f"{parent_req.contract.symbol} with stop_loss={stop_req.stop_price} "
            f"take_profit={take_profit_req.limit_price}"
        )

        # Qualify contract
        base_contract = self._create_contract(parent_req.contract)
        qualified_contracts = await self.ib.qualifyContractsAsync(base_contract)
        if not qualified_contracts:
            raise ValueError(f"Unable to qualify contract for symbol {parent_req.contract.symbol}")
        contract = qualified_contracts[0]

        # Create parent order (transmit=False to group with children)
        parent_order_req = OrderRequest(
            contract=parent_req.contract,
            side=parent_req.side,
            quantity=parent_req.quantity,
            order_type=parent_req.order_type,
            limit_price=parent_req.limit_price,
            stop_price=parent_req.stop_price,
            time_in_force=parent_req.time_in_force,
            transmit=False,  # Don't transmit yet - will transmit with children
        )
        parent_order = self._create_order(parent_order_req)

        # Place parent order (not transmitted yet)
        parent_trade = self.ib.placeOrder(contract, parent_order)
        parent_order_id = parent_trade.order.orderId

        # Create stop loss order (child)
        stop_order = self._create_order(stop_req)
        stop_order.parentId = parent_order_id
        stop_order.transmit = False  # Will be transmitted with take profit

        # Create take profit order (child, transmit=True to send all three)
        take_profit_order = self._create_order(take_profit_req)
        take_profit_order.parentId = parent_order_id
        take_profit_order.transmit = True  # Transmit all orders

        # Place child orders
        stop_trade = self.ib.placeOrder(contract, stop_order)
        take_profit_trade = self.ib.placeOrder(contract, take_profit_order)

        stop_order_id = stop_trade.order.orderId
        take_profit_order_id = take_profit_trade.order.orderId

        logger.info(
            f"Bracket order placed: parent={parent_order_id}, "
            f"stop_loss={stop_order_id}, take_profit={take_profit_order_id}"
        )

        # Wait for parent order acknowledgement
        loop = asyncio.get_running_loop()

        def _handle_fill(trade_obj: Trade, fill: Fill) -> None:  # pragma: no cover - callback
            try:
                exec_side = (
                    OrderSide.BUY
                    if fill.execution.side.upper() in {"BOT", "BUY"}
                    else OrderSide.SELL
                )
                quantity = int(fill.execution.shares)
                fill_price = Decimal(str(fill.execution.price))
                commission_value = Decimal("0")
                if fill.commissionReport is not None:
                    commission_value = Decimal(str(fill.commissionReport.commission))

                event = ExecutionEvent(
                    order_id=trade_obj.order.orderId,
                    contract=parent_req.contract,
                    side=exec_side,
                    quantity=quantity,
                    price=fill_price,
                    commission=commission_value,
                    timestamp=datetime.now(UTC),
                )
                loop.create_task(self._publish_execution(event))
            except Exception as exc:
                logger.warning("Failed to handle execution event: %s", exc)

        def _handle_commission(
            trade_obj: Trade, fill: Fill, report: CommissionReport
        ) -> None:  # pragma: no cover - callback
            try:
                exec_side = (
                    OrderSide.BUY
                    if fill.execution.side.upper() in {"BOT", "BUY"}
                    else OrderSide.SELL
                )
                quantity = int(fill.execution.shares)
                fill_price = Decimal(str(fill.execution.price))
                commission_value = Decimal(str(report.commission))
                event = ExecutionEvent(
                    order_id=trade_obj.order.orderId,
                    contract=parent_req.contract,
                    side=exec_side,
                    quantity=quantity,
                    price=fill_price,
                    commission=commission_value,
                    timestamp=datetime.now(UTC),
                )
                loop.create_task(self._publish_execution(event))
            except Exception as exc:
                logger.warning("Failed to handle commission report: %s", exc)

        # Attach callbacks to parent order
        parent_trade.fillEvent += _handle_fill
        parent_trade.commissionReportEvent += _handle_commission

        # Wait for order acknowledgement
        status_event = getattr(parent_trade, "statusEvent", None)
        try:
            if status_event is not None:
                await asyncio.wait_for(status_event, timeout=5)
            else:
                await self.ib.sleep(1)
        except TimeoutError:
            logger.warning(
                "Timed out waiting for bracket order acknowledgement from IBKR "
                f"(parent_order_id={parent_order_id})"
            )

        # Get parent order status
        ib_status = parent_trade.orderStatus
        result_status = self._map_order_status(ib_status.status)
        filled_quantity = int(getattr(ib_status, "filled", 0) or 0)
        remaining_quantity = int(getattr(ib_status, "remaining", 0) or 0)
        avg_fill_price = float(getattr(ib_status, "avgFillPrice", 0.0) or 0.0)

        # Create result with child order IDs
        result = OrderResult(
            order_id=parent_order_id,
            contract=parent_req.contract,
            side=parent_req.side,
            quantity=parent_req.quantity,
            order_type=parent_req.order_type,
            status=result_status,
            filled_quantity=filled_quantity,
            avg_fill_price=Decimal(str(avg_fill_price)),
            parent_order_id=None,  # This is the parent
            child_order_ids=[stop_order_id, take_profit_order_id],
        )

        logger.info(
            f"Bracket order confirmed - Parent: {result.order_id}, "
            f"Children: {result.child_order_ids}"
        )

        # Publish order status event
        await self._publish_order_status(
            OrderStatusEvent(
                order_id=result.order_id,
                status=result_status,
                contract=parent_req.contract,
                side=parent_req.side,
                filled=filled_quantity,
                remaining=remaining_quantity,
                avg_fill_price=avg_fill_price,
                timestamp=datetime.now(UTC),
            )
        )

        return result

    async def get_positions(self) -> list[Position]:
        """Get current positions.

        Returns:
            List of current positions
        """
        self._ensure_connected()

        ib_positions = await self.ib.reqPositionsAsync()
        positions: list[Position] = []

        for ib_pos in ib_positions:
            contract = SymbolContract(
                symbol=ib_pos.contract.symbol,
                sec_type=ib_pos.contract.secType,
                exchange=ib_pos.contract.exchange,
                currency=ib_pos.contract.currency,
            )

            position = Position(
                contract=contract,
                quantity=int(ib_pos.position),
                avg_cost=Decimal(str(ib_pos.avgCost)),
                market_value=Decimal(str(ib_pos.marketValue)),
                unrealized_pnl=Decimal(str(ib_pos.unrealizedPNL)),
            )
            positions.append(position)

        return positions

    async def get_account_summary(self) -> dict[str, Any]:
        """Get account summary information.

        Returns:
            Dictionary with account values
        """
        self._ensure_connected()

        summary_items = await self.ib.accountSummaryAsync()
        summary = {}
        for item in summary_items:
            summary[item.tag] = item.value
        return summary

    def __enter__(self) -> IBKRBroker:
        """Context manager entry."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Context manager exit."""
        if self._connected:
            self.ib.disconnect()

    def _map_order_status(self, status: str) -> OrderStatus:
        try:
            return OrderStatus(status)
        except ValueError:
            logger.debug(f"Unmapped IBKR order status '{status}' - treating as SUBMITTED")
            return OrderStatus.SUBMITTED

    async def _publish_order_status(self, event: OrderStatusEvent) -> None:
        if self._event_bus is None:
            return
        await self._event_bus.publish(EventTopic.ORDER_STATUS, event)

    async def _publish_execution(self, event: ExecutionEvent) -> None:
        if self._event_bus is None:
            return
        await self._event_bus.publish(EventTopic.EXECUTION, event)
