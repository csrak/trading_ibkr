"""Industry Model Strategy Example.

ML-based trading strategy using peer correlation to forecast returns.

USAGE:
    # 1. Train the model first
    ibkr-trader train-model \
      --target AAPL \
      --peer MSFT \
      --peer GOOGL \
      --start 2023-01-01 \
      --end 2024-01-01 \
      --horizon 5 \
      --artifact-dir model/artifacts/aapl_model

    # 2. Run strategy with trained model
    python examples/strategies/industry_model.py

    # 3. Live trade with model
    ibkr-trader run --config examples/configs/industry_model.json
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from ibkr_trader.base_strategy import BaseStrategy, BrokerProtocol
from ibkr_trader.models import OrderRequest, OrderSide, OrderType, SymbolContract


class IndustryModelExample(BaseStrategy):
    """ML-based strategy using industry peer correlation.

    APPROACH:
    - Train linear model on peer returns → target future price
    - Use model predictions to forecast expected returns
    - BUY when expected return > entry_threshold
    - SELL when expected return < -entry_threshold or position held > max_hold_days

    PARAMETERS:
    - model_artifact_path: Path to trained model JSON
    - entry_threshold: Minimum expected return to enter (0.01 = 1%)
    - exit_threshold: Return threshold to exit (0.005 = 0.5%)
    - position_size: Shares per trade
    - max_hold_days: Maximum days to hold position
    """

    def __init__(
        self,
        symbol: str,
        model_artifact_path: str | Path,
        entry_threshold: float = 0.01,
        exit_threshold: float = 0.005,
        position_size: int = 10,
        max_hold_days: int = 5,
    ) -> None:
        """Initialize industry model strategy.

        Args:
            symbol: Trading symbol (must match model target)
            model_artifact_path: Path to trained model JSON
            entry_threshold: Minimum expected return to enter (default 1%)
            exit_threshold: Return threshold to exit (default 0.5%)
            position_size: Shares per trade
            max_hold_days: Maximum days to hold position
        """
        self.symbol = symbol
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.position_size = position_size
        self.max_hold_days = max_hold_days

        # Load trained model
        model_path = Path(model_artifact_path)
        if not model_path.exists():
            raise FileNotFoundError(f"Model artifact not found: {model_path}")

        with model_path.open("r", encoding="utf-8") as fp:
            model_data = json.load(fp)

        self.target = model_data["target"]
        self.peers = model_data["peers"]
        self.intercept = model_data["intercept"]
        self.coefficients = model_data["coefficients"]
        self.horizon_days = model_data["horizon_days"]

        if self.target != symbol:
            raise ValueError(
                f"Model target ({self.target}) doesn't match symbol ({symbol})"
            )

        # Position tracking
        self.entry_price: float | None = None
        self.entry_bar_count: int = 0
        self.last_prediction: float | None = None

        # Price history for peer returns (simplified - in production use actual peer data)
        self.current_price: float | None = None

    async def on_bar(
        self, symbol: str, price: Decimal, broker: BrokerProtocol
    ) -> None:
        """Process price update and manage positions.

        Args:
            symbol: Symbol of price update
            price: Current price
            broker: Broker for order execution
        """
        if symbol != self.symbol:
            return

        price_float = float(price)
        self.current_price = price_float

        # Get current position
        position = await self.get_position(symbol, broker)

        if position == 0:
            # Look for entry
            await self._check_entry(price, broker)
        else:
            # Track holding period
            self.entry_bar_count += 1
            # Look for exit
            await self._check_exit(position, price, broker)

    async def _check_entry(
        self, price: Decimal, broker: BrokerProtocol
    ) -> None:
        """Check for entry signals.

        Args:
            price: Current price
            broker: Broker for order execution
        """
        # In production, fetch actual peer returns and make prediction
        # Here we simulate with a placeholder prediction
        # Real implementation would:
        # 1. Get latest peer returns from market data
        # 2. Apply model: prediction = intercept + sum(coeff * peer_return)
        # 3. Calculate expected_return = (prediction - current_price) / current_price

        # Placeholder: assume we have peer data and made prediction
        expected_return = self._simulate_prediction(float(price))
        self.last_prediction = expected_return

        if expected_return > self.entry_threshold:
            # Strong bullish signal - BUY
            await self._enter_position(OrderSide.BUY, price, broker)
            print(
                f"[Industry Model] ENTRY LONG: expected_return={expected_return:.2%} "
                f"@ ${price}"
            )

        elif expected_return < -self.entry_threshold:
            # Strong bearish signal - SELL SHORT
            await self._enter_position(OrderSide.SELL, price, broker)
            print(
                f"[Industry Model] ENTRY SHORT: expected_return={expected_return:.2%} "
                f"@ ${price}"
            )

    async def _check_exit(
        self, position: int, price: Decimal, broker: BrokerProtocol
    ) -> None:
        """Check for exit signals.

        Args:
            position: Current position
            price: Current price
            broker: Broker for order execution
        """
        # Exit if max hold period reached
        if self.entry_bar_count >= self.max_hold_days:
            await self._close_position(position, price, broker)
            print(
                f"[Industry Model] EXIT (max hold): "
                f"held {self.entry_bar_count} bars @ ${price}"
            )
            return

        # Exit if expected return drops below threshold
        expected_return = self._simulate_prediction(float(price))
        self.last_prediction = expected_return

        if position > 0 and expected_return < self.exit_threshold:
            # Long position, signal weakening
            await self._close_position(position, price, broker)
            print(
                f"[Industry Model] EXIT LONG: expected_return={expected_return:.2%} "
                f"@ ${price}"
            )

        elif position < 0 and expected_return > -self.exit_threshold:
            # Short position, signal weakening
            await self._close_position(position, price, broker)
            print(
                f"[Industry Model] EXIT SHORT: expected_return={expected_return:.2%} "
                f"@ ${price}"
            )

    def _simulate_prediction(self, current_price: float) -> float:
        """Simulate model prediction (placeholder for real implementation).

        In production, this would:
        1. Fetch latest peer prices/returns
        2. Apply trained model: prediction = intercept + sum(coeff * peer_return)
        3. Calculate expected_return = (prediction - current_price) / current_price

        Args:
            current_price: Current price of target symbol

        Returns:
            Simulated expected return
        """
        # Placeholder: simulate with random noise around zero
        # Real implementation would use actual peer data
        import random

        return random.uniform(-0.03, 0.03)

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
        self.entry_bar_count = 0

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
        self.entry_bar_count = 0


# Example usage
if __name__ == "__main__":
    import asyncio

    from ibkr_trader.events import EventBus
    from ibkr_trader.sim.broker import SimulatedBroker

    async def test_strategy():
        """Test industry model with simulated data."""
        event_bus = EventBus()
        broker = SimulatedBroker(event_bus)

        # NOTE: This example uses a placeholder model artifact
        # In production, you would train a real model first using:
        # ibkr-trader train-model --target AAPL --peer MSFT --peer GOOGL ...

        # For demo purposes, create a minimal model artifact
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "test_model.json"
            model_data = {
                "target": "AAPL",
                "peers": ["MSFT", "GOOGL"],
                "horizon_days": 5,
                "intercept": 150.0,
                "coefficients": {"MSFT": 0.5, "GOOGL": 0.3},
                "train_start": "2023-01-01",
                "train_end": "2024-01-01",
                "created_at": "2024-01-01T00:00:00Z",
                "prediction_path": "predictions.csv",
            }
            with model_path.open("w", encoding="utf-8") as fp:
                json.dump(model_data, fp)

            strategy = IndustryModelExample(
                "AAPL",
                model_artifact_path=model_path,
                entry_threshold=0.015,
                exit_threshold=0.005,
            )

            # Simulate price series with trend
            prices = [
                Decimal("100"),
                Decimal("101"),
                Decimal("102"),
                Decimal("103"),
                Decimal("105"),  # Uptrend (should trigger BUY)
                Decimal("106"),
                Decimal("107"),
                Decimal("106"),  # Weakening (might exit)
                Decimal("105"),
                Decimal("104"),
            ]

            print("Testing industry model strategy...")
            for i, price in enumerate(prices):
                print(f"\nBar {i+1}: Price=${price}")
                await strategy.on_bar("AAPL", price, broker)

                position = await strategy.get_position("AAPL", broker)
                print(f"Position: {position} shares")
                if strategy.last_prediction is not None:
                    print(f"Last prediction: {strategy.last_prediction:.2%}")

            print("\nTest complete!")

    asyncio.run(test_strategy())
