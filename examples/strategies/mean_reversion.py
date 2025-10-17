"""Mean Reversion Strategy Example.

Statistical arbitrage strategy using z-score based entry/exit with dynamic stops.

USAGE:
    # Via config file
    ibkr-trader run --config examples/configs/mean_reversion.json

    # Direct instantiation (see main block below)
    python examples/strategies/mean_reversion.py
"""

from collections import deque
from decimal import Decimal
from statistics import fmean, pstdev

from ibkr_trader.base_strategy import BaseStrategy, BrokerProtocol
from ibkr_trader.models import OrderRequest, OrderSide, OrderType, SymbolContract


class MeanReversionExample(BaseStrategy):
    """Z-score based mean reversion with stop loss.

    ENTRY LOGIC:
    - Calculate rolling mean and std dev
    - Compute z-score: (price - mean) / std
    - BUY when z-score < -entry_threshold (oversold)
    - SELL when z-score > +entry_threshold (overbought)

    EXIT LOGIC:
    - Close when z-score returns to exit_threshold
    - Stop loss at stop_multiple * std dev

    PARAMETERS:
    - lookback: Window for statistics (20-60 typical)
    - entry_zscore: Entry threshold (1.5-2.5 typical)
    - exit_zscore: Exit threshold (0.3-0.8 typical)
    - stop_multiple: Stop loss distance (1.5-3.0 typical)
    """

    def __init__(
        self,
        symbol: str,
        lookback: int = 20,
        entry_zscore: float = 2.0,
        exit_zscore: float = 0.5,
        stop_multiple: float = 2.0,
        position_size: int = 10,
    ) -> None:
        """Initialize mean reversion strategy.

        Args:
            symbol: Trading symbol
            lookback: Statistical window size
            entry_zscore: Z-score threshold for entry (absolute value)
            exit_zscore: Z-score threshold for exit (absolute value)
            stop_multiple: Stop loss distance in multiples of std dev
            position_size: Shares per trade
        """
        self.symbol = symbol
        self.lookback = lookback
        self.entry_zscore = entry_zscore
        self.exit_zscore = exit_zscore
        self.stop_multiple = stop_multiple
        self.position_size = position_size

        # Price history
        self.prices = deque(maxlen=lookback)

        # Entry tracking
        self.entry_price: float | None = None
        self.entry_side: OrderSide | None = None

    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        """Process price update and manage positions.

        Args:
            symbol: Symbol of price update
            price: Current price
            broker: Broker for order execution
        """
        if symbol != self.symbol:
            return

        price_float = float(price)
        self.prices.append(price_float)

        # Need full lookback window
        if len(self.prices) < self.lookback:
            return

        # Calculate statistics
        mean = fmean(self.prices)
        std = pstdev(self.prices)
        if std < 1e-9:  # Avoid division by zero
            return

        zscore = (price_float - mean) / std

        # Get current position
        position = await self.get_position(symbol, broker)

        # Check stop loss first
        if await self._check_stop_loss(position, price_float, std, broker):
            return

        # Position management
        if position == 0:
            # Look for entry
            await self._check_entry(zscore, price, broker)
        else:
            # Look for exit
            await self._check_exit(zscore, position, price, broker)

    async def _check_entry(
        self, zscore: float, price: Decimal, broker: BrokerProtocol
    ) -> None:
        """Check for entry signals.

        Args:
            zscore: Current z-score
            price: Current price
            broker: Broker for order execution
        """
        if zscore <= -self.entry_zscore:
            # Oversold - BUY
            await self._enter_position(OrderSide.BUY, price, broker)
            print(f"[Mean Reversion] ENTRY LONG: z={zscore:.2f} @ ${price}")

        elif zscore >= self.entry_zscore:
            # Overbought - SELL (short)
            await self._enter_position(OrderSide.SELL, price, broker)
            print(f"[Mean Reversion] ENTRY SHORT: z={zscore:.2f} @ ${price}")

    async def _check_exit(
        self, zscore: float, position: int, price: Decimal, broker: BrokerProtocol
    ) -> None:
        """Check for exit signals.

        Args:
            zscore: Current z-score
            position: Current position
            price: Current price
            broker: Broker for order execution
        """
        # Exit when z-score returns toward mean
        if abs(zscore) <= self.exit_zscore:
            await self._close_position(position, price, broker)
            print(f"[Mean Reversion] EXIT: z={zscore:.2f} @ ${price}")

    async def _check_stop_loss(
        self, position: int, current_price: float, std: float, broker: BrokerProtocol
    ) -> bool:
        """Check and execute stop loss if triggered.

        Args:
            position: Current position
            current_price: Current price
            std: Current standard deviation
            broker: Broker for order execution

        Returns:
            True if stop loss was triggered
        """
        if position == 0 or self.entry_price is None:
            return False

        # Calculate stop distance
        stop_distance = self.stop_multiple * std

        # Check if stop triggered
        if position > 0:  # Long position
            if current_price < self.entry_price - stop_distance:
                await self._close_position(position, Decimal(str(current_price)), broker)
                print(
                    f"[Mean Reversion] STOP LOSS LONG: "
                    f"${current_price:.2f} (entry: ${self.entry_price:.2f})"
                )
                return True

        elif position < 0:  # Short position
            if current_price > self.entry_price + stop_distance:
                await self._close_position(position, Decimal(str(current_price)), broker)
                print(
                    f"[Mean Reversion] STOP LOSS SHORT: "
                    f"${current_price:.2f} (entry: ${self.entry_price:.2f})"
                )
                return True

        return False

    async def _enter_position(
        self, side: OrderSide, price: Decimal, broker: BrokerProtocol
    ) -> None:
        """Enter new position.

        Args:
            side: BUY or SELL
            price: Entry price
            broker: Broker for order execution
        """
        order = OrderRequest(
            contract=SymbolContract(symbol=self.symbol),
            side=side,
            quantity=self.position_size,
            order_type=OrderType.MARKET,
            expected_price=price,
        )
        await broker.place_order(order)

        self.entry_price = float(price)
        self.entry_side = side

    async def _close_position(
        self, position: int, price: Decimal, broker: BrokerProtocol
    ) -> None:
        """Close existing position.

        Args:
            position: Current position size
            price: Exit price
            broker: Broker for order execution
        """
        if position == 0:
            return

        side = OrderSide.SELL if position > 0 else OrderSide.BUY
        order = OrderRequest(
            contract=SymbolContract(symbol=self.symbol),
            side=side,
            quantity=abs(position),
            order_type=OrderType.MARKET,
            expected_price=price,
        )
        await broker.place_order(order)

        # Reset entry tracking
        self.entry_price = None
        self.entry_side = None


# Example usage
if __name__ == "__main__":
    import asyncio

    from ibkr_trader.events import EventBus
    from ibkr_trader.sim.broker import SimulatedBroker

    async def test_strategy():
        """Test mean reversion with simulated data."""
        event_bus = EventBus()
        broker = SimulatedBroker(event_bus)
        strategy = MeanReversionExample(
            "AAPL", lookback=10, entry_zscore=1.5, exit_zscore=0.5
        )

        # Simulate mean-reverting price series
        prices = [
            Decimal("100"),
            Decimal("101"),
            Decimal("102"),
            Decimal("103"),
            Decimal("104"),
            Decimal("105"),  # Build up mean
            Decimal("108"),  # Spike up (overbought)
            Decimal("106"),
            Decimal("104"),
            Decimal("103"),  # Revert to mean
            Decimal("102"),
            Decimal("98"),  # Spike down (oversold)
            Decimal("100"),
            Decimal("102"),  # Revert to mean
        ]

        print("Testing mean reversion strategy...")
        for i, price in enumerate(prices):
            print(f"\nBar {i+1}: Price=${price}")
            await strategy.on_bar("AAPL", price, broker)

            position = await strategy.get_position("AAPL", broker)
            print(f"Position: {position} shares")

        print("\nTest complete!")

    asyncio.run(test_strategy())
