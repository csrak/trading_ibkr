# Strategy Quick Start - Copy/Paste Templates

## Template 1: Simple Price-Based Strategy

**Use when:** You only need tick-by-tick price data (live trading or backtesting)

```python
from ibkr_trader.base_strategy import BaseStrategy, BrokerProtocol
from ibkr_trader.models import OrderRequest, OrderSide, OrderType, SymbolContract
from decimal import Decimal

class MyPriceStrategy(BaseStrategy):
    """Template for simple price-based strategies"""

    def __init__(self, symbol: str, param1: float, param2: int):
        self.symbol = symbol
        self.param1 = param1
        self.param2 = param2
        # Add your state variables here
        self.price_buffer = []

    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        """Called for each price update"""
        if symbol != self.symbol:
            return

        # 1. Update your indicators/state
        self.price_buffer.append(float(price))

        # 2. Check if ready to trade
        if len(self.price_buffer) < self.param2:
            return

        # 3. Get current position
        position = await self.get_position(symbol, broker)

        # 4. Generate signals and place orders
        if self.should_buy() and position <= 0:
            order = OrderRequest(
                contract=SymbolContract(symbol=symbol),
                side=OrderSide.BUY,
                quantity=10,
                order_type=OrderType.MARKET
            )
            await broker.place_order(order)

        elif self.should_sell() and position >= 0:
            order = OrderRequest(
                contract=SymbolContract(symbol=symbol),
                side=OrderSide.SELL,
                quantity=10,
                order_type=OrderType.MARKET
            )
            await broker.place_order(order)

    def should_buy(self) -> bool:
        """Replace with your buy logic"""
        return False  # TODO: Implement

    def should_sell(self) -> bool:
        """Replace with your sell logic"""
        return False  # TODO: Implement
```

**Run it live:**
```python
from ibkr_trader.strategy import Strategy, StrategyConfig
from ibkr_trader.execution import IBKRBroker
from ibkr_trader.core import EventBus

# Wrap in live Strategy class
class MyLiveStrategy(Strategy):
    def __init__(self, config, broker, event_bus, risk_guard=None):
        super().__init__(config, broker, event_bus, risk_guard)
        self.impl = MyPriceStrategy(config.symbols[0], param1=1.5, param2=20)

    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        await self.impl.on_bar(symbol, price, broker)

# Then use via CLI or programmatically
```

---

## Template 2: Market Making Strategy

**Use when:** You need L2 order book data (market replay only)

```python
from ibkr_trader.sim.runner import ReplayStrategy
from ibkr_trader.base_strategy import BrokerProtocol
from ibkr_trader.models import OrderRequest, OrderSide, OrderType, SymbolContract
from model.data.models import OrderBookSnapshot
from decimal import Decimal

class MarketMakerStrategy(ReplayStrategy):
    """Template for market making strategies"""

    def __init__(self, symbol: str, spread: float, inventory_limit: int):
        self.symbol = symbol
        self.spread = spread
        self.inventory_limit = inventory_limit
        self.inventory = 0

    async def on_order_book(self, snapshot: OrderBookSnapshot, broker: BrokerProtocol) -> None:
        """Called for each order book snapshot"""
        if snapshot.symbol != self.symbol or not snapshot.levels:
            return

        # 1. Extract bid/ask from order book
        best_bid = max(
            (level.price for level in snapshot.levels if level.side.value == "bid"),
            default=None
        )
        best_ask = min(
            (level.price for level in snapshot.levels if level.side.value == "ask"),
            default=None
        )

        if best_bid is None or best_ask is None:
            return

        # 2. Calculate mid and quote prices
        mid = (best_bid + best_ask) / 2
        bid_price = mid - self.spread / 2
        ask_price = mid + self.spread / 2

        # 3. Manage inventory limits
        if self.inventory < self.inventory_limit:
            await self.post_bid(broker, bid_price)

        if self.inventory > -self.inventory_limit:
            await self.post_ask(broker, ask_price)

    async def on_fill(self, side: OrderSide, quantity: int) -> None:
        """Track inventory on fills"""
        self.inventory += quantity if side == OrderSide.BUY else -quantity

    async def post_bid(self, broker: BrokerProtocol, price: float) -> None:
        order = OrderRequest(
            contract=SymbolContract(symbol=self.symbol),
            side=OrderSide.BUY,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=Decimal(str(price))
        )
        await broker.place_order(order)

    async def post_ask(self, broker: BrokerProtocol, price: float) -> None:
        order = OrderRequest(
            contract=SymbolContract(symbol=self.symbol),
            side=OrderSide.SELL,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=Decimal(str(price))
        )
        await broker.place_order(order)
```

**Run it in replay:**
```python
from ibkr_trader.sim.runner import ReplayRunner
from ibkr_trader.sim.events import EventLoader

loader = EventLoader(order_book_files=["path/to/order_book.csv"])
strategy = MarketMakerStrategy(symbol="AAPL", spread=0.1, inventory_limit=5)
runner = ReplayRunner(loader, strategy)

await runner.run()
```

---

## Template 3: Mean Reversion with Stop Loss

**Use when:** You want z-score based entry/exit with risk management

```python
from ibkr_trader.base_strategy import BaseStrategy, BrokerProtocol
from ibkr_trader.models import OrderRequest, OrderSide, OrderType, SymbolContract
from decimal import Decimal
from statistics import fmean, pstdev

class MeanReversionStrategy(BaseStrategy):
    """Z-score mean reversion with stop loss"""

    def __init__(
        self,
        symbol: str,
        lookback: int = 20,
        entry_zscore: float = 2.0,
        exit_zscore: float = 0.5,
        stop_multiple: float = 2.0
    ):
        self.symbol = symbol
        self.lookback = lookback
        self.entry_zscore = entry_zscore
        self.exit_zscore = exit_zscore
        self.stop_multiple = stop_multiple
        self.prices = []
        self.entry_price = None

    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        if symbol != self.symbol:
            return

        self.prices.append(float(price))

        if len(self.prices) < self.lookback:
            return

        recent = self.prices[-self.lookback:]
        mean = fmean(recent)
        std = pstdev(recent) or 1e-9
        zscore = (float(price) - mean) / std

        position = await self.get_position(symbol, broker)

        # Stop loss
        if self.entry_price and abs(float(price) - self.entry_price) > self.stop_multiple * std:
            if position != 0:
                await self.close_position(broker, position)
            return

        # Entry
        if position == 0:
            if zscore <= -self.entry_zscore:  # Oversold
                await self.open_position(broker, OrderSide.BUY)
                self.entry_price = float(price)
            elif zscore >= self.entry_zscore:  # Overbought
                await self.open_position(broker, OrderSide.SELL)
                self.entry_price = float(price)

        # Exit
        elif abs(zscore) <= self.exit_zscore:
            await self.close_position(broker, position)

    async def open_position(self, broker: BrokerProtocol, side: OrderSide) -> None:
        order = OrderRequest(
            contract=SymbolContract(symbol=self.symbol),
            side=side,
            quantity=10,
            order_type=OrderType.MARKET
        )
        await broker.place_order(order)

    async def close_position(self, broker: BrokerProtocol, position: int) -> None:
        if position == 0:
            return
        side = OrderSide.SELL if position > 0 else OrderSide.BUY
        order = OrderRequest(
            contract=SymbolContract(symbol=self.symbol),
            side=side,
            quantity=abs(position),
            order_type=OrderType.MARKET
        )
        await broker.place_order(order)
        self.entry_price = None
```

---

## Template 4: ML Model Integration

**Use when:** You have a trained model and want to use predictions for trading

```python
from ibkr_trader.base_strategy import BaseStrategy, BrokerProtocol
from ibkr_trader.models import OrderRequest, OrderSide, OrderType, SymbolContract
from decimal import Decimal
from pathlib import Path
import pickle
import numpy as np

class MLStrategy(BaseStrategy):
    """Template for ML model-based strategies"""

    def __init__(self, symbol: str, model_path: Path, feature_window: int = 20):
        self.symbol = symbol
        self.feature_window = feature_window

        # Load your trained model
        with open(model_path, 'rb') as f:
            self.model = pickle.load(f)

        self.price_buffer = []

    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        if symbol != self.symbol:
            return

        self.price_buffer.append(float(price))

        if len(self.price_buffer) < self.feature_window:
            return

        # 1. Extract features
        features = self.extract_features(self.price_buffer[-self.feature_window:])

        # 2. Get prediction
        prediction = self.model.predict([features])[0]

        # 3. Trade on prediction
        position = await self.get_position(symbol, broker)

        if prediction > 0.6 and position <= 0:  # Strong buy signal
            order = OrderRequest(
                contract=SymbolContract(symbol=symbol),
                side=OrderSide.BUY,
                quantity=10,
                order_type=OrderType.MARKET
            )
            await broker.place_order(order)

        elif prediction < 0.4 and position >= 0:  # Strong sell signal
            order = OrderRequest(
                contract=SymbolContract(symbol=symbol),
                side=OrderSide.SELL,
                quantity=10,
                order_type=OrderType.MARKET
            )
            await broker.place_order(order)

    def extract_features(self, prices: list[float]) -> np.ndarray:
        """Extract features from price history"""
        # Example: returns, volatility, momentum
        returns = np.diff(prices) / prices[:-1]
        volatility = np.std(returns)
        momentum = (prices[-1] - prices[0]) / prices[0]

        return np.array([
            prices[-1],      # Last price
            volatility,      # Recent volatility
            momentum,        # Momentum
            np.mean(returns) # Average return
        ])
```

---

## Template 5: Multi-Symbol Portfolio Strategy

**Use when:** You want to trade multiple symbols with rebalancing logic

```python
from ibkr_trader.base_strategy import BaseStrategy, BrokerProtocol
from ibkr_trader.models import OrderRequest, OrderSide, OrderType, SymbolContract
from decimal import Decimal
from typing import Dict

class PortfolioStrategy(BaseStrategy):
    """Template for multi-symbol portfolio strategies"""

    def __init__(self, symbols: list[str], rebalance_window: int = 50):
        self.symbols = symbols
        self.rebalance_window = rebalance_window
        self.prices: Dict[str, list[float]] = {s: [] for s in symbols}
        self.target_weights: Dict[str, float] = {s: 1.0 / len(symbols) for s in symbols}

    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        if symbol not in self.symbols:
            return

        self.prices[symbol].append(float(price))

        # Wait for all symbols to have enough data
        if any(len(self.prices[s]) < self.rebalance_window for s in self.symbols):
            return

        # Only rebalance on first symbol's update (to avoid multiple rebalances per bar)
        if symbol != self.symbols[0]:
            return

        await self.rebalance_portfolio(broker)

    async def rebalance_portfolio(self, broker: BrokerProtocol) -> None:
        """Rebalance portfolio to target weights"""
        # 1. Calculate target weights based on momentum/volatility/etc.
        self.target_weights = self.calculate_target_weights()

        # 2. Get current positions
        current_positions = {}
        for symbol in self.symbols:
            current_positions[symbol] = await self.get_position(symbol, broker)

        # 3. Calculate total portfolio value (simplified)
        total_value = sum(
            current_positions[s] * self.prices[s][-1]
            for s in self.symbols
        )

        if total_value == 0:
            total_value = 100000  # Initial capital

        # 4. Rebalance to target weights
        for symbol in self.symbols:
            target_value = total_value * self.target_weights[symbol]
            current_value = current_positions[symbol] * self.prices[symbol][-1]
            diff_value = target_value - current_value

            if abs(diff_value) > 100:  # Only rebalance if difference is significant
                quantity = int(diff_value / self.prices[symbol][-1])
                if quantity > 0:
                    await self.place_order(broker, symbol, OrderSide.BUY, abs(quantity))
                elif quantity < 0:
                    await self.place_order(broker, symbol, OrderSide.SELL, abs(quantity))

    def calculate_target_weights(self) -> Dict[str, float]:
        """Calculate target weights (replace with your logic)"""
        # Example: equal weight all symbols
        return {s: 1.0 / len(self.symbols) for s in self.symbols}

        # Or momentum-based:
        # momentums = {s: (self.prices[s][-1] - self.prices[s][0]) / self.prices[s][0]
        #              for s in self.symbols}
        # total_momentum = sum(max(0, m) for m in momentums.values())
        # return {s: max(0, momentums[s]) / total_momentum for s in self.symbols}

    async def place_order(
        self,
        broker: BrokerProtocol,
        symbol: str,
        side: OrderSide,
        quantity: int
    ) -> None:
        order = OrderRequest(
            contract=SymbolContract(symbol=symbol),
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET
        )
        await broker.place_order(order)
```

---

## Quick Command Reference

### Run Strategy in Backtest
```bash
# Create CSV with columns: timestamp, close
# Then run:
ibkr-trader backtest data.csv --symbol AAPL --strategy sma --fast 10 --slow 20
```

### Run Strategy Live (currently only SMA supported)
```bash
# Make sure TWS/Gateway is running on paper port 7497
ibkr-trader run --symbol AAPL --fast 10 --slow 20
```

### Train ML Model
```bash
ibkr-trader train-model \
  --target AAPL \
  --peer MSFT \
  --peer GOOGL \
  --start 2023-01-01 \
  --end 2024-01-01 \
  --horizon 5
```

---

## Testing Your Strategy

### Unit Test Template
```python
import pytest
from unittest.mock import AsyncMock
from decimal import Decimal

@pytest.mark.asyncio
async def test_my_strategy_buys_on_signal():
    # Arrange
    strategy = MyPriceStrategy(symbol="AAPL", param1=1.5, param2=3)
    broker = AsyncMock()
    broker.get_positions.return_value = []

    # Act - simulate price bars
    await strategy.on_bar("AAPL", Decimal("100.0"), broker)
    await strategy.on_bar("AAPL", Decimal("101.0"), broker)
    await strategy.on_bar("AAPL", Decimal("102.0"), broker)

    # Assert
    assert broker.place_order.called
    order = broker.place_order.call_args[0][0]
    assert order.side == OrderSide.BUY
```

### Integration Test with SimulatedBroker
```python
from ibkr_trader.sim.broker import SimulatedBroker
from ibkr_trader.core import EventBus

@pytest.mark.asyncio
async def test_strategy_execution():
    event_bus = EventBus()
    broker = SimulatedBroker(event_bus)
    strategy = MyPriceStrategy(symbol="AAPL", param1=1.5, param2=3)

    await strategy.on_bar("AAPL", Decimal("100.0"), broker)
    await strategy.on_bar("AAPL", Decimal("101.0"), broker)
    await strategy.on_bar("AAPL", Decimal("102.0"), broker)

    positions = await broker.get_positions()
    assert len(positions) > 0
```

---

## Next Steps

1. Copy the template that matches your use case
2. Implement your signal generation logic in `should_buy()` / `should_sell()`
3. Test with SimulatedBroker
4. Backtest with historical CSV data
5. Run live in paper trading mode
6. Graduate to live trading (with extreme caution!)

For detailed explanations, see [Unified Strategy Guide](./unified_strategy_guide.md)
