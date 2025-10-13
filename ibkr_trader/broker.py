"""IBKR broker connection and order management."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ib_insync import Contract, IB, LimitOrder, MarketOrder, Order, OrderState, StopOrder
from loguru import logger

from ibkr_trader.config import IBKRConfig
from ibkr_trader.events import EventBus, EventTopic, OrderStatusEvent
from ibkr_trader.models import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    SymbolContract,
)
from ibkr_trader.safety import LiveTradingGuard


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
        except asyncio.TimeoutError as exc:
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
            return MarketOrder(action, quantity)
        elif order_request.order_type == OrderType.LIMIT:
            return LimitOrder(action, quantity, float(order_request.limit_price or 0))
        elif order_request.order_type == OrderType.STOP:
            return StopOrder(action, quantity, float(order_request.stop_price or 0))
        else:
            raise ValueError(f"Unsupported order type: {order_request.order_type}")

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
            symbol=order_request.contract.symbol,
            quantity=order_request.quantity
        )

        self._ensure_connected()

        # Log order details
        logger.info(
            f"Placing order: {order_request.side.value} {order_request.quantity} "
            f"{order_request.contract.symbol} @ {order_request.order_type.value}"
        )

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

        status_event = getattr(trade, "statusEvent", None)

        try:
            if status_event is not None:
                await asyncio.wait_for(status_event, timeout=5)
            else:
                # Fall back to short sleep if trade object lacks status events
                await self.ib.sleep(1)
        except asyncio.TimeoutError:
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

    def __enter__(self) -> "IBKRBroker":
        """Context manager entry."""
        return self

    def __exit__(self, *args: Any) -> None:
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
