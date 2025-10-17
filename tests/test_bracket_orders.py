"""Tests for bracket order functionality."""

from decimal import Decimal

import pytest

from ibkr_trader.models import (
    BracketOrderRequest,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    SymbolContract,
)


class TestBracketOrderRequest:
    """Tests for BracketOrderRequest validation."""

    def test_valid_bracket_order_long_position(self) -> None:
        """Test valid bracket order for long position."""
        parent = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.MARKET,
        )
        stop_loss = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.STOP,
            stop_price=Decimal("145.00"),
        )
        take_profit = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("155.00"),
        )

        bracket = BracketOrderRequest(
            parent=parent,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        assert bracket.parent.side == OrderSide.BUY
        assert bracket.stop_loss.side == OrderSide.SELL
        assert bracket.take_profit.side == OrderSide.SELL
        assert bracket.parent.quantity == 10
        assert bracket.stop_loss.quantity == 10
        assert bracket.take_profit.quantity == 10

    def test_valid_bracket_order_short_position(self) -> None:
        """Test valid bracket order for short position."""
        parent = OrderRequest(
            contract=SymbolContract(symbol="TSLA"),
            side=OrderSide.SELL,
            quantity=5,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("250.00"),
        )
        stop_loss = OrderRequest(
            contract=SymbolContract(symbol="TSLA"),
            side=OrderSide.BUY,
            quantity=5,
            order_type=OrderType.STOP,
            stop_price=Decimal("260.00"),
        )
        take_profit = OrderRequest(
            contract=SymbolContract(symbol="TSLA"),
            side=OrderSide.BUY,
            quantity=5,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("240.00"),
        )

        bracket = BracketOrderRequest(
            parent=parent,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        assert bracket.parent.side == OrderSide.SELL
        assert bracket.stop_loss.side == OrderSide.BUY
        assert bracket.take_profit.side == OrderSide.BUY

    def test_reject_stop_loss_same_side_as_parent(self) -> None:
        """Test that stop loss must be opposite side from parent."""
        parent = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.MARKET,
        )
        stop_loss = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,  # Wrong side!
            quantity=10,
            order_type=OrderType.STOP,
            stop_price=Decimal("145.00"),
        )
        take_profit = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("155.00"),
        )

        with pytest.raises(ValueError, match="Stop loss must be opposite side from parent"):
            BracketOrderRequest(
                parent=parent,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

    def test_reject_take_profit_same_side_as_parent(self) -> None:
        """Test that take profit must be opposite side from parent."""
        parent = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.MARKET,
        )
        stop_loss = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.STOP,
            stop_price=Decimal("145.00"),
        )
        take_profit = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,  # Wrong side!
            quantity=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("155.00"),
        )

        with pytest.raises(ValueError, match="Take profit must be opposite side from parent"):
            BracketOrderRequest(
                parent=parent,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

    def test_reject_stop_loss_quantity_mismatch(self) -> None:
        """Test that stop loss quantity must match parent."""
        parent = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.MARKET,
        )
        stop_loss = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=5,  # Wrong quantity!
            order_type=OrderType.STOP,
            stop_price=Decimal("145.00"),
        )
        take_profit = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("155.00"),
        )

        with pytest.raises(ValueError, match="Stop loss quantity must match parent"):
            BracketOrderRequest(
                parent=parent,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

    def test_reject_take_profit_quantity_mismatch(self) -> None:
        """Test that take profit quantity must match parent."""
        parent = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.MARKET,
        )
        stop_loss = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.STOP,
            stop_price=Decimal("145.00"),
        )
        take_profit = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=15,  # Wrong quantity!
            order_type=OrderType.LIMIT,
            limit_price=Decimal("155.00"),
        )

        with pytest.raises(ValueError, match="Take profit quantity must match parent"):
            BracketOrderRequest(
                parent=parent,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

    def test_reject_stop_loss_not_stop_order(self) -> None:
        """Test that stop loss must be STOP or STOP_LIMIT order."""
        parent = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.MARKET,
        )
        stop_loss = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.MARKET,  # Wrong type!
        )
        take_profit = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("155.00"),
        )

        with pytest.raises(ValueError, match="Stop loss must be STOP or STOP_LIMIT"):
            BracketOrderRequest(
                parent=parent,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

    def test_reject_take_profit_not_limit_order(self) -> None:
        """Test that take profit must be LIMIT order."""
        parent = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.MARKET,
        )
        stop_loss = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.STOP,
            stop_price=Decimal("145.00"),
        )
        take_profit = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.MARKET,  # Wrong type!
        )

        with pytest.raises(ValueError, match="Take profit must be LIMIT order"):
            BracketOrderRequest(
                parent=parent,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

    def test_stop_loss_with_stop_limit_order(self) -> None:
        """Test that STOP_LIMIT orders are valid for stop loss."""
        parent = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.MARKET,
        )
        stop_loss = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.STOP_LIMIT,
            stop_price=Decimal("145.00"),
            limit_price=Decimal("144.50"),
        )
        take_profit = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("155.00"),
        )

        bracket = BracketOrderRequest(
            parent=parent,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        assert bracket.stop_loss.order_type == OrderType.STOP_LIMIT
        assert bracket.stop_loss.stop_price == Decimal("145.00")
        assert bracket.stop_loss.limit_price == Decimal("144.50")


class TestBracketOrderBroker:
    """Tests for bracket order broker integration."""

    @pytest.mark.asyncio
    async def test_place_bracket_order_creates_three_orders(self) -> None:
        """Test that bracket order creates parent and two child orders."""
        from unittest.mock import AsyncMock, MagicMock, Mock

        from ib_insync import Contract, Order, Trade
        from ib_insync import OrderStatus as IBOrderStatus

        from ibkr_trader.broker import IBKRBroker
        from ibkr_trader.config import IBKRConfig, TradingMode
        from ibkr_trader.safety import LiveTradingGuard

        config = IBKRConfig(
            trading_mode=TradingMode.PAPER,
            host="127.0.0.1",
            port=7497,
            client_id=1,
        )
        guard = LiveTradingGuard(config=config, live_flag_enabled=False)
        guard.acknowledge_live_trading()

        # Mock IB client
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        mock_ib.qualifyContractsAsync = AsyncMock(return_value=[Contract()])
        mock_ib.sleep = AsyncMock()

        # Mock three trade objects (parent + 2 children)
        parent_trade = Mock(spec=Trade)
        parent_trade.order = Mock(spec=Order)
        parent_trade.order.orderId = 100
        parent_trade.orderStatus = Mock(spec=IBOrderStatus)
        parent_trade.orderStatus.status = "Submitted"
        parent_trade.orderStatus.filled = 0
        parent_trade.orderStatus.remaining = 10
        parent_trade.orderStatus.avgFillPrice = 0.0
        parent_trade.statusEvent = None
        parent_trade.fillEvent = MagicMock()
        parent_trade.commissionReportEvent = MagicMock()

        stop_trade = Mock(spec=Trade)
        stop_trade.order = Mock(spec=Order)
        stop_trade.order.orderId = 101

        take_profit_trade = Mock(spec=Trade)
        take_profit_trade.order = Mock(spec=Order)
        take_profit_trade.order.orderId = 102

        # Mock placeOrder to return the three trades
        order_call_count = 0

        def mock_place_order(contract: object, order: object) -> Mock:
            nonlocal order_call_count
            order_call_count += 1
            if order_call_count == 1:
                return parent_trade
            if order_call_count == 2:
                return stop_trade
            return take_profit_trade

        mock_ib.placeOrder = Mock(side_effect=mock_place_order)

        broker = IBKRBroker(config=config, guard=guard, ib_client=mock_ib)
        broker._connected = True

        # Create bracket order request
        parent = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.MARKET,
        )
        stop_loss = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.STOP,
            stop_price=Decimal("145.00"),
        )
        take_profit = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("155.00"),
        )

        bracket_request = BracketOrderRequest(
            parent=parent,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        # Place bracket order
        result = await broker.place_bracket_order(bracket_request)

        # Verify result
        assert result.order_id == 100
        assert result.side == OrderSide.BUY
        assert result.quantity == 10
        assert result.status == OrderStatus.SUBMITTED
        assert result.parent_order_id is None
        assert result.child_order_ids == [101, 102]

        # Verify three orders were placed
        assert mock_ib.placeOrder.call_count == 3

    @pytest.mark.asyncio
    async def test_bracket_order_respects_safety_guards(self) -> None:
        """Test that bracket orders go through safety validation."""
        from unittest.mock import MagicMock

        from ibkr_trader.broker import IBKRBroker
        from ibkr_trader.config import IBKRConfig, TradingMode
        from ibkr_trader.safety import LiveTradingError, LiveTradingGuard

        config = IBKRConfig(
            trading_mode=TradingMode.PAPER,
            host="127.0.0.1",
            port=7497,
            client_id=1,
            max_position_size=5,  # Set low limit
        )
        guard = LiveTradingGuard(config=config, live_flag_enabled=False)
        guard.acknowledge_live_trading()

        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True

        broker = IBKRBroker(config=config, guard=guard, ib_client=mock_ib)
        broker._connected = True

        # Create bracket order that exceeds position limit
        parent = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,
            quantity=10,  # Exceeds max_position_size=5
            order_type=OrderType.MARKET,
        )
        stop_loss = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.STOP,
            stop_price=Decimal("145.00"),
        )
        take_profit = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,
            quantity=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("155.00"),
        )

        bracket_request = BracketOrderRequest(
            parent=parent,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        # Should raise LiveTradingError due to position size limit
        with pytest.raises(LiveTradingError, match="exceeds max position size"):
            await broker.place_bracket_order(bracket_request)

    @pytest.mark.asyncio
    async def test_bracket_order_sets_parent_child_relationships(self) -> None:
        """Test that child orders have parentId set correctly."""
        from unittest.mock import AsyncMock, MagicMock, Mock

        from ib_insync import Contract, Order, Trade
        from ib_insync import OrderStatus as IBOrderStatus

        from ibkr_trader.broker import IBKRBroker
        from ibkr_trader.config import IBKRConfig, TradingMode
        from ibkr_trader.safety import LiveTradingGuard

        config = IBKRConfig(
            trading_mode=TradingMode.PAPER,
            host="127.0.0.1",
            port=7497,
            client_id=1,
        )
        guard = LiveTradingGuard(config=config, live_flag_enabled=False)
        guard.acknowledge_live_trading()

        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        mock_ib.qualifyContractsAsync = AsyncMock(return_value=[Contract()])
        mock_ib.sleep = AsyncMock()

        parent_trade = Mock(spec=Trade)
        parent_trade.order = Mock(spec=Order)
        parent_trade.order.orderId = 200
        parent_trade.orderStatus = Mock(spec=IBOrderStatus)
        parent_trade.orderStatus.status = "Submitted"
        parent_trade.orderStatus.filled = 0
        parent_trade.orderStatus.remaining = 10
        parent_trade.orderStatus.avgFillPrice = 0.0
        parent_trade.statusEvent = None
        parent_trade.fillEvent = MagicMock()
        parent_trade.commissionReportEvent = MagicMock()

        stop_trade = Mock(spec=Trade)
        stop_trade.order = Mock(spec=Order)
        stop_trade.order.orderId = 201

        take_profit_trade = Mock(spec=Trade)
        take_profit_trade.order = Mock(spec=Order)
        take_profit_trade.order.orderId = 202

        placed_orders: list[Order] = []

        def mock_place_order(contract: object, order: Order) -> Mock:
            placed_orders.append(order)
            if len(placed_orders) == 1:
                return parent_trade
            if len(placed_orders) == 2:
                return stop_trade
            return take_profit_trade

        mock_ib.placeOrder = Mock(side_effect=mock_place_order)

        broker = IBKRBroker(config=config, guard=guard, ib_client=mock_ib)
        broker._connected = True

        parent = OrderRequest(
            contract=SymbolContract(symbol="MSFT"),
            side=OrderSide.BUY,
            quantity=5,
            order_type=OrderType.MARKET,
        )
        stop_loss = OrderRequest(
            contract=SymbolContract(symbol="MSFT"),
            side=OrderSide.SELL,
            quantity=5,
            order_type=OrderType.STOP,
            stop_price=Decimal("300.00"),
        )
        take_profit = OrderRequest(
            contract=SymbolContract(symbol="MSFT"),
            side=OrderSide.SELL,
            quantity=5,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("320.00"),
        )

        bracket_request = BracketOrderRequest(
            parent=parent,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        await broker.place_bracket_order(bracket_request)

        # Verify parent order has transmit=False
        assert placed_orders[0].transmit is False

        # Verify child orders have parentId set
        assert placed_orders[1].parentId == 200  # Stop loss
        assert placed_orders[2].parentId == 200  # Take profit

        # Verify stop loss has transmit=False
        assert placed_orders[1].transmit is False

        # Verify take profit has transmit=True (transmits all three)
        assert placed_orders[2].transmit is True
