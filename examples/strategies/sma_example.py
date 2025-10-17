"""Simple Moving Average (SMA) Strategy Example.

This is a complete, ready-to-run implementation of a classic trend-following
strategy using moving average crossovers.

USAGE:
    # Backtest
    ibkr-trader backtest data/AAPL.csv --strategy sma --fast 10 --slow 20

    # Paper trade
    ibkr-trader run --symbol AAPL --fast 10 --slow 20

    # Live trade (requires confirmation)
    IBKR_TRADING_MODE=live ibkr-trader run --symbol AAPL --fast 10 --slow 20 --live
"""

from collections import deque
from decimal import Decimal

from ibkr_trader.base_strategy import BaseStrategy, BrokerProtocol
from ibkr_trader.models import OrderRequest, OrderSide, OrderType, SymbolContract


class SimpleMovingAverageExample(BaseStrategy):
    """Classic SMA crossover strategy.

    BUY when fast MA crosses above slow MA (golden cross)
    SELL when fast MA crosses below slow MA (death cross)

    This implementation:
    - Maintains two moving averages using efficient deques
    - Only trades on crossover events (not every bar)
    - Manages position state (flat/long/short)
    - Logs all signals for debugging
    """

    def __init__(
        self,
        symbol: str,
        fast_period: int = 10,
        slow_period: int = 20,
        position_size: int = 10,
    ) -> None:
        """Initialize SMA strategy.

        Args:
            symbol: Trading symbol (e.g., "AAPL")
            fast_period: Short-term moving average period
            slow_period: Long-term moving average period
            position_size: Number of shares per trade
        """
        self.symbol = symbol
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.position_size = position_size

        # Price buffers (efficient deques with maxlen)
        self.fast_prices = deque(maxlen=fast_period)
        self.slow_prices = deque(maxlen=slow_period)

        # State tracking
        self.last_signal: str | None = None  # 'BUY', 'SELL', or None

    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        """Process new price bar and generate signals.

        Args:
            symbol: Symbol of the price update
            price: Current price
            broker: Broker for order execution
        """
        if symbol != self.symbol:
            return

        # Update price buffers
        price_float = float(price)
        self.fast_prices.append(price_float)
        self.slow_prices.append(price_float)

        # Need enough data for both MAs
        if len(self.fast_prices) < self.fast_period or len(self.slow_prices) < self.slow_period:
            return

        # Calculate moving averages
        fast_ma = sum(self.fast_prices) / len(self.fast_prices)
        slow_ma = sum(self.slow_prices) / len(self.slow_prices)

        # Get current position
        position = await self.get_position(symbol, broker)

        # Generate signals
        signal = self._generate_signal(fast_ma, slow_ma, position)

        if signal == "BUY":
            await self._execute_buy(symbol, price, broker)
        elif signal == "SELL":
            await self._execute_sell(symbol, position, price, broker)

    def _generate_signal(self, fast_ma: float, slow_ma: float, position: int) -> str | None:
        """Determine trading signal based on MA crossover.

        Args:
            fast_ma: Fast moving average value
            slow_ma: Slow moving average value
            position: Current position (positive=long, negative=short, 0=flat)

        Returns:
            'BUY', 'SELL', or None
        """
        # Golden cross (fast crosses above slow) + flat or short
        if fast_ma > slow_ma and position <= 0 and self.last_signal != "BUY":
            self.last_signal = "BUY"
            return "BUY"

        # Death cross (fast crosses below slow) + long position
        if fast_ma < slow_ma and position > 0 and self.last_signal != "SELL":
            self.last_signal = "SELL"
            return "SELL"

        return None

    async def _execute_buy(
        self, symbol: str, price: Decimal, broker: BrokerProtocol
    ) -> None:
        """Execute buy order.

        Args:
            symbol: Trading symbol
            price: Current price (for logging)
            broker: Broker for order execution
        """
        order = OrderRequest(
            contract=SymbolContract(symbol=symbol),
            side=OrderSide.BUY,
            quantity=self.position_size,
            order_type=OrderType.MARKET,
            expected_price=price,
        )
        await broker.place_order(order)
        print(f"[SMA] BUY signal: {self.position_size} shares @ ${price}")

    async def _execute_sell(
        self, symbol: str, position: int, price: Decimal, broker: BrokerProtocol
    ) -> None:
        """Execute sell order (close long position).

        Args:
            symbol: Trading symbol
            position: Current position size
            price: Current price (for logging)
            broker: Broker for order execution
        """
        if position == 0:
            return  # Nothing to sell

        order = OrderRequest(
            contract=SymbolContract(symbol=symbol),
            side=OrderSide.SELL,
            quantity=abs(position),
            order_type=OrderType.MARKET,
            expected_price=price,
        )
        await broker.place_order(order)
        print(f"[SMA] SELL signal: {abs(position)} shares @ ${price}")


# Example usage in a standalone script
if __name__ == "__main__":
    import asyncio

    from ibkr_trader.events import EventBus
    from ibkr_trader.sim.broker import SimulatedBroker

    async def test_strategy():
        """Test strategy with simulated broker and mock prices."""
        event_bus = EventBus()
        broker = SimulatedBroker(event_bus)
        strategy = SimpleMovingAverageExample("AAPL", fast_period=3, slow_period=5)

        # Simulate price series (downtrend then uptrend)
        prices = [
            Decimal("100"),
            Decimal("99"),
            Decimal("98"),
            Decimal("97"),
            Decimal("96"),  # Downtrend complete
            Decimal("97"),
            Decimal("98"),
            Decimal("99"),
            Decimal("100"),  # Should trigger BUY around here
            Decimal("101"),
        ]

        print("Starting SMA strategy test...")
        for i, price in enumerate(prices):
            print(f"\nBar {i+1}: Price=${price}")
            await strategy.on_bar("AAPL", price, broker)

            # Show position
            position = await strategy.get_position("AAPL", broker)
            print(f"Position: {position} shares")

        print("\nTest complete!")

    asyncio.run(test_strategy())
