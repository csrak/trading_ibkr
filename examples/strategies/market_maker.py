"""Market Maker Strategy Example.

Basic market making with fixed spread and inventory management.

USAGE:
    # Via config file with order book data
    ibkr-trader run --config examples/configs/fixed_spread_mm.json

    # Direct instantiation (see main block below)
    python examples/strategies/market_maker.py

REQUIREMENTS:
    - L2 order book data (requires IBKR market depth subscription)
    - See docs/order_book_implementation.md for setup
"""

from __future__ import annotations

from decimal import Decimal

from ibkr_trader.base_strategy import BaseStrategy, BrokerProtocol
from ibkr_trader.models import OrderRequest, OrderSide, OrderType, SymbolContract


class MarketMakerExample(BaseStrategy):
    """Basic market making with fixed spread and inventory limits.

    LOGIC:
    - Quote bid/ask around mid price with fixed spread
    - Maintain inventory within limits
    - Skew quotes when inventory builds up
    - Cancel and replace quotes on each order book update

    PARAMETERS:
    - spread: Bid-ask spread in dollars (default: 0.10)
    - quote_size: Size per level (default: 1 share)
    - inventory_limit: Max absolute position (default: 5)
    - skew_factor: Inventory skew adjustment (default: 0.01)

    NOTE: This is a simplified example. Production market makers need:
    - Advanced inventory management
    - Dynamic spread based on volatility
    - Multiple price levels
    - Fill simulation and adverse selection modeling
    """

    def __init__(
        self,
        symbol: str,
        spread: float = 0.10,
        quote_size: int = 1,
        inventory_limit: int = 5,
        skew_factor: float = 0.01,
    ) -> None:
        """Initialize market maker strategy.

        Args:
            symbol: Trading symbol
            spread: Bid-ask spread in dollars
            quote_size: Size per quote level
            inventory_limit: Max absolute position
            skew_factor: How much to skew quotes per unit inventory
        """
        self.symbol = symbol
        self.spread = spread
        self.quote_size = quote_size
        self.inventory_limit = inventory_limit
        self.skew_factor = skew_factor

        # State tracking
        self.mid_price: float | None = None
        self.bid_order_id: int | None = None
        self.ask_order_id: int | None = None

    async def on_bar(
        self, symbol: str, price: Decimal, broker: BrokerProtocol
    ) -> None:
        """Process price update and quote both sides.

        Args:
            symbol: Symbol of price update
            price: Current mid price
            broker: Broker for order execution
        """
        if symbol != self.symbol:
            return

        self.mid_price = float(price)

        # Get current position (inventory)
        position = await self.get_position(symbol, broker)

        # Check inventory limits
        if abs(position) >= self.inventory_limit:
            print(
                f"[Market Maker] Inventory limit reached: {position} shares "
                f"(limit: {self.inventory_limit})"
            )
            # Cancel all quotes when at limit
            await self._cancel_quotes(broker)
            return

        # Quote both sides
        await self._update_quotes(position, broker)

    async def _update_quotes(
        self, position: int, broker: BrokerProtocol
    ) -> None:
        """Update bid/ask quotes based on current inventory.

        Args:
            position: Current position (inventory)
            broker: Broker for order execution
        """
        if self.mid_price is None:
            return

        # Calculate inventory skew
        # Positive inventory → skew quotes lower to sell
        # Negative inventory → skew quotes higher to buy
        inventory_skew = position * self.skew_factor

        # Calculate quote prices
        half_spread = self.spread / 2
        bid_price = self.mid_price - half_spread - inventory_skew
        ask_price = self.mid_price + half_spread - inventory_skew

        # Cancel existing quotes (simplified - in production use replace/amend)
        await self._cancel_quotes(broker)

        # Place new quotes
        bid_order = OrderRequest(
            contract=SymbolContract(symbol=self.symbol),
            side=OrderSide.BUY,
            quantity=self.quote_size,
            order_type=OrderType.LIMIT,
            limit_price=Decimal(str(round(bid_price, 2))),
        )

        ask_order = OrderRequest(
            contract=SymbolContract(symbol=self.symbol),
            side=OrderSide.SELL,
            quantity=self.quote_size,
            order_type=OrderType.LIMIT,
            limit_price=Decimal(str(round(ask_price, 2))),
        )

        # Submit orders (in production, track order IDs for cancellation)
        await broker.place_order(bid_order)
        await broker.place_order(ask_order)

        print(
            f"[Market Maker] Quotes: BID ${bid_price:.2f} x {self.quote_size} | "
            f"ASK ${ask_price:.2f} x {self.quote_size} | "
            f"Position: {position} | Mid: ${self.mid_price:.2f}"
        )

    async def _cancel_quotes(self, broker: BrokerProtocol) -> None:
        """Cancel all outstanding quotes.

        Args:
            broker: Broker for order cancellation
        """
        # In production, maintain order ID tracking and cancel specific orders
        # For this example, we assume orders are short-lived or use IOC/FOK
        pass


# Example usage
if __name__ == "__main__":
    import asyncio

    from ibkr_trader.events import EventBus
    from ibkr_trader.sim.broker import SimulatedBroker

    async def test_strategy():
        """Test market maker with simulated price data."""
        event_bus = EventBus()
        broker = SimulatedBroker(event_bus)
        strategy = MarketMakerExample(
            "AAPL", spread=0.10, quote_size=1, inventory_limit=3
        )

        # Simulate price series with some movement
        prices = [
            Decimal("100.00"),
            Decimal("100.05"),
            Decimal("100.10"),
            Decimal("100.05"),
            Decimal("100.00"),
            Decimal("99.95"),
            Decimal("100.00"),
        ]

        print("Testing market maker strategy...")
        print("Note: In production, this requires L2 order book data\n")

        for i, price in enumerate(prices):
            print(f"\n=== Bar {i+1}: Mid Price=${price} ===")
            await strategy.on_bar("AAPL", price, broker)

            # Simulate some fills (in production, these come from executions)
            if i == 2:
                # Simulate buy fill
                print("[SIM] Buy order filled - inventory +1")
                broker._positions["AAPL"] = 1
            elif i == 4:
                # Simulate another buy fill
                print("[SIM] Buy order filled - inventory +2")
                broker._positions["AAPL"] = 2

            position = await strategy.get_position("AAPL", broker)
            print(f"Current inventory: {position} shares")

        print("\n=== Test complete! ===")
        print(
            "\nProduction deployment requires:"
            "\n1. L2 order book subscription (see docs/order_book_implementation.md)"
            "\n2. Order ID tracking for cancellation"
            "\n3. Fill handling with on_execution callback"
            "\n4. Risk controls (max loss, daily limits)"
        )

    asyncio.run(test_strategy())
