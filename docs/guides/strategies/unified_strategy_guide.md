# Unified Strategy Interface Guide

## Overview

The IBKR Personal Trader now uses a **unified strategy interface** that allows the same strategy code to run across three execution contexts:

1. **Live Trading** - Real execution via IBKRBroker connected to TWS/Gateway
2. **Backtesting** - Simulated execution via SimulatedBroker replaying CSV price data
3. **Market Replay** - High-fidelity simulation via MockBroker replaying order book/trade/option surface events

This guide shows you how to write strategies that work seamlessly in all three contexts.

---

## Core Concepts

### BrokerProtocol

All broker implementations (`IBKRBroker`, `SimulatedBroker`, `MockBroker`) satisfy the `BrokerProtocol`:

```python
from ibkr_trader.base_strategy import BrokerProtocol

# Any broker provides these methods:
async def place_order(self, request: OrderRequest) -> OrderResult
async def get_positions(self) -> list[Position]
```

### BaseStrategy

All strategies extend `BaseStrategy` and implement callback methods for different data types:

```python
from ibkr_trader.base_strategy import BaseStrategy, BrokerProtocol
from decimal import Decimal

class MyStrategy(BaseStrategy):
    # Implement only the callbacks you need

    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        """Called for each price update (live & backtest)"""
        position = await self.get_position(symbol, broker)
        if self.should_buy(price) and position <= 0:
            await broker.place_order(...)

    async def on_order_book(self, snapshot: OrderBookSnapshot, broker: BrokerProtocol) -> None:
        """Called for L2 order book updates (replay only)"""
        pass

    async def on_trade(self, trade: TradeEvent, broker: BrokerProtocol) -> None:
        """Called for individual trade events (replay only)"""
        pass

    async def on_option_surface(self, entry: OptionSurfaceEntry, broker: BrokerProtocol) -> None:
        """Called for option surface updates (replay only)"""
        pass

    async def on_fill(self, side: OrderSide, quantity: int) -> None:
        """Called when any order fills (all contexts)"""
        pass
```

**Key Points:**
- All callbacks are **optional** - implement only what you need
- The `broker` parameter is passed to callbacks - use it to submit orders
- Use `await self.get_position(symbol, broker)` to query current positions
- Strategies are broker-agnostic - the same code runs everywhere

---

## Strategy Types

### 1. Simple Price-Based Strategies

For strategies that only need tick-by-tick price data:

```python
from ibkr_trader.base_strategy import BaseStrategy, BrokerProtocol
from decimal import Decimal

class SimpleMovingAverageStrategy(BaseStrategy):
    def __init__(self, fast_period: int, slow_period: int):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.price_history = []

    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        self.price_history.append(price)

        if len(self.price_history) < self.slow_period:
            return

        fast_sma = sum(self.price_history[-self.fast_period:]) / self.fast_period
        slow_sma = sum(self.price_history[-self.slow_period:]) / self.slow_period

        position = await self.get_position(symbol, broker)

        if fast_sma > slow_sma and position <= 0:
            # Buy signal
            order = OrderRequest(
                contract=SymbolContract(symbol=symbol),
                side=OrderSide.BUY,
                quantity=10,
                order_type=OrderType.MARKET
            )
            await broker.place_order(order)
        elif fast_sma < slow_sma and position >= 0:
            # Sell signal
            order = OrderRequest(
                contract=SymbolContract(symbol=symbol),
                side=OrderSide.SELL,
                quantity=10,
                order_type=OrderType.MARKET
            )
            await broker.place_order(order)
```

**Usage:**
- ✅ Works in **live trading** (receives real-time prices from IBKR)
- ✅ Works in **backtesting** (receives historical prices from CSV)
- ❌ Not applicable to **market replay** (no price bar data in replay context)

---

### 1b. Using OHLC Data in Strategies

**New in 2025-10-21**: The `on_bar()` callback now supports optional OHLC (Open-High-Low-Close) data for advanced technical indicators.

#### Overview

While the `price` parameter represents the close price, you can access high/low/volume data via `**kwargs` for calculations like:
- ATR (Average True Range) - requires high/low
- VWAP (Volume Weighted Average Price) - requires volume
- Bollinger Bands - benefits from high/low
- Candlestick patterns - requires OHLC

#### Basic Usage

```python
from ibkr_trader.base_strategy import BaseStrategy, BrokerProtocol
from decimal import Decimal

class VolatilityStrategy(BaseStrategy):
    def __init__(self):
        self.highs = []
        self.lows = []
        self.closes = []

    async def on_bar(
        self, symbol: str, price: Decimal, broker: BrokerProtocol, **kwargs: object
    ) -> None:
        # Extract OHLC data from kwargs
        high = kwargs.get("high", price)  # Falls back to price if not available
        low = kwargs.get("low", price)    # Falls back to price if not available
        volume = kwargs.get("volume")     # May be None

        # Store for calculations
        self.highs.append(high)
        self.lows.append(low)
        self.closes.append(price)

        # Calculate ATR (simplified)
        if len(self.highs) >= 14:
            recent_ranges = [
                self.highs[i] - self.lows[i]
                for i in range(-14, 0)
            ]
            atr = sum(recent_ranges) / Decimal("14")

            # Use ATR for position sizing
            if self.should_buy(price):
                quantity = self.calculate_position_size(atr)
                await broker.place_order(...)
```

#### MarketDataEvent OHLC Fields

The `MarketDataEvent` now includes:

```python
@dataclass(frozen=True, slots=True)
class MarketDataEvent:
    symbol: str
    price: Decimal           # Close price (required)
    timestamp: datetime      # Bar timestamp (required)
    high: Decimal | None     # Bar high (optional, defaults to price)
    low: Decimal | None      # Bar low (optional, defaults to price)
    volume: int | None       # Bar volume (optional, may be None)
```

#### Backward Compatibility

**All existing strategies continue to work without modification:**

```python
# Old signature still works
async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
    # This strategy works fine, just won't receive OHLC data
    pass

# New signature with OHLC support
async def on_bar(
    self, symbol: str, price: Decimal, broker: BrokerProtocol, **kwargs: object
) -> None:
    # This strategy can access OHLC when available
    high = kwargs.get("high", price)
    low = kwargs.get("low", price)
```

#### Real-World Example: Adaptive Momentum Strategy

The `AdaptiveMomentumStrategy` uses real OHLC data for ATR calculation:

```python
async def on_bar(
    self, symbol: str, price: Decimal, broker: BrokerProtocol, **kwargs: object
) -> None:
    # Extract OHLC data
    high = kwargs.get("high", price)
    low = kwargs.get("low", price)

    # Store in history buffers
    self._price_history[symbol].append(price)
    self._high_history[symbol].append(high)
    self._low_history[symbol].append(low)

    # Calculate volatility using real high/low data
    volatility = atr(
        prices=self._price_history[symbol],
        highs=self._high_history[symbol],
        lows=self._low_history[symbol],
        period=self.config.atr_lookback,
    )

    # Use volatility for position sizing
    target_qty = await self._compute_target_quantity(symbol, momentum, volatility)
```

#### Type Safety

All kwargs are typed as `object` for mypy compatibility. Cast to `Decimal` when extracting:

```python
async def on_bar(
    self, symbol: str, price: Decimal, broker: BrokerProtocol, **kwargs: object
) -> None:
    high_raw = kwargs.get("high")
    if high_raw is not None:
        assert isinstance(high_raw, Decimal)
        high = high_raw
    else:
        high = price  # Fallback
```

#### When OHLC Data is Available

| Context | OHLC Available? | Notes |
|---------|----------------|-------|
| Live trading (real-time) | Partial | Currently only price; OHLC coming with bar data subscription |
| Live trading (1-min bars) | Yes | When using bar-based market data |
| Backtesting | Yes | When CSV includes OHLC columns |
| Market replay | Yes | Historical bar data includes OHLC |
| Mock data | Configurable | Mock generator can emit OHLC |

#### Helper Functions

The `ibkr_trader/strategies/factors.py` module provides OHLC-based indicators:

```python
from ibkr_trader.strategies.factors import atr, momentum_signal, vwap

# ATR using high/low data
volatility = atr(
    prices=price_history,
    highs=high_history,
    lows=low_history,
    period=14
)

# Momentum using close prices
momentum = momentum_signal(
    prices=price_history,
    fast=5,
    slow=20
)

# VWAP using OHLC+Volume data (NEW in 2025-10-21)
vwap_value = vwap(
    prices=price_history,
    highs=high_history,
    lows=low_history,
    volumes=volume_history,
    period=20
)

# Price deviation from VWAP (mean reversion signal)
deviation_pct = (current_price - vwap_value) / vwap_value * Decimal("100")
if deviation_pct < -2:  # Price 2% below VWAP
    # Potential buy signal (mean reversion)
    pass
```

---

### Dynamic Universe Management with Screeners

Strategies can automatically refresh their trading universe using screeners. The `AdaptiveMomentumStrategy` supports periodic screener refresh to adapt to changing market conditions.

#### Configuration

Set the refresh interval in your strategy config:

```python
from ibkr_trader.strategies.config import AdaptiveMomentumConfig

config = AdaptiveMomentumConfig(
    name="adaptive_momentum",
    symbols=["AAPL", "MSFT"],  # Initial universe
    screener_refresh_seconds=900,  # Refresh every 15 minutes (default)
    # ... other config
)
```

**Key parameters:**
- `screener_refresh_seconds`: How often to run the screener (in seconds)
  - Default: `900` (15 minutes)
  - Set to `0` to disable automatic refresh
  - Minimum recommended: `60` seconds to avoid excessive API calls

#### Attaching a Screener

Connect a `LiquidityScreener` (or any screener) to your strategy:

```python
from ibkr_trader.strategies.adaptive_momentum import AdaptiveMomentumStrategy
from ibkr_trader.data import LiquidityScreener, LiquidityScreenerConfig
from model.data.client import MarketDataClient
from decimal import Decimal

# Configure screener
screener_config = LiquidityScreenerConfig(
    minimum_dollar_volume=Decimal("100000000"),  # $100M daily volume
    minimum_price=Decimal("20"),
    max_symbols=20,
    lookback_days=5,
)

# Create screener with market data client
market_data_client = MarketDataClient(...)
screener = LiquidityScreener(screener_config, market_data_client)

# Attach to strategy
strategy = AdaptiveMomentumStrategy(config, broker, event_bus)
strategy.set_screener(screener)

# Start strategy - screener refresh task starts automatically
await strategy.start()
```

#### How It Works

1. **On startup**: If a screener is attached and `screener_refresh_seconds > 0`, a background task is created
2. **Periodic refresh**: Every N seconds, the screener runs and returns a new symbol list
3. **Universe update**: The strategy's `_symbols` set is updated with the new symbols
4. **Buffer management**: History buffers are preserved for existing symbols, created for new ones
5. **Telemetry**: Each refresh emits a `{namespace}.screen_refresh` event with the new universe

#### Refresh Behavior

```python
async def refresh_universe(self) -> None:
    """Called automatically by the scheduler."""
    if self._screener is None:
        return

    result = await self._screener.run()
    new_symbols = {symbol.upper() for symbol in result.symbols}

    if not new_symbols:
        logger.warning("Screener returned empty universe; retaining previous symbols")
        return  # Keeps current symbols on empty result

    self._symbols = new_symbols  # Update universe
    # Buffers updated for new symbols...
```

**Safety features:**
- Empty screener results are rejected (preserves current universe)
- Errors during refresh are logged but don't crash the strategy
- Screener failures don't disrupt trading on existing symbols

#### Manual Refresh

You can also trigger a refresh manually:

```python
# In your strategy logic
if some_condition:
    await self.refresh_universe()
```

#### Lifecycle Management

The screener task is automatically managed:

```python
# Starting the strategy
await strategy.start()
# → Calls super().start() to begin market data subscription
# → Creates screener refresh task if configured

# Stopping the strategy
await strategy.stop()
# → Cancels screener refresh task gracefully
# → Calls super().stop() to clean up market data subscription
```

#### Telemetry Integration

Each screener refresh emits telemetry:

```json
{
  "event": "adaptive_momentum.screen_refresh",
  "context": {
    "symbols": ["AAPL", "MSFT", "GOOGL", "TSLA"],
    "generated_at": "2025-10-21T12:00:00Z",
    "metadata": {
      "data_source": "real",
      "lookback_days": 5,
      "universe_size": 4
    }
  }
}
```

On errors:

```json
{
  "event": "adaptive_momentum.screener_error",
  "level": "error",
  "context": {
    "error": "Connection timeout to market data service"
  }
}
```

#### Testing Screener Integration

Example test using mocked screener:

```python
import pytest
from unittest.mock import Mock, AsyncMock
from ibkr_trader.data import ScreenerResult
from datetime import UTC, datetime

@pytest.mark.asyncio
async def test_screener_refresh():
    # Create mock screener
    mock_screener = Mock()
    mock_screener.run = AsyncMock(
        return_value=ScreenerResult(
            symbols=["AAPL", "GOOGL", "TSLA"],
            generated_at=datetime.now(UTC),
            metadata={"test": True},
        )
    )

    strategy = AdaptiveMomentumStrategy(config, broker, event_bus)
    strategy.set_screener(mock_screener)

    await strategy.start()
    await asyncio.sleep(1.5)  # Wait for refresh

    # Verify universe updated
    assert strategy._symbols == {"AAPL", "GOOGL", "TSLA"}

    await strategy.stop()
```

See `tests/test_adaptive_momentum.py` for comprehensive screener scheduler tests.

---

### 2. Advanced Microstructure Strategies

For strategies that need L2 order book depth:

```python
from ibkr_trader.base_strategy import BaseStrategy, BrokerProtocol
from model.data.models import OrderBookSnapshot

class MarketMakingStrategy(BaseStrategy):
    def __init__(self, symbol: str, spread: float):
        self.symbol = symbol
        self.spread = spread

    async def on_order_book(self, snapshot: OrderBookSnapshot, broker: BrokerProtocol) -> None:
        if snapshot.symbol != self.symbol:
            return

        # Calculate mid price from order book
        best_bid = max((l.price for l in snapshot.levels if l.side.value == "bid"), default=None)
        best_ask = min((l.price for l in snapshot.levels if l.side.value == "ask"), default=None)

        if best_bid is None or best_ask is None:
            return

        mid = (best_bid + best_ask) / 2

        # Post quotes around mid
        await self.post_bid(broker, mid - self.spread / 2)
        await self.post_ask(broker, mid + self.spread / 2)

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

**Usage:**
- ❌ Not applicable to **live trading** (no L2 data in live event bus yet)
- ❌ Not applicable to **backtesting** (no L2 data in CSV bars)
- ✅ Works in **market replay** (replays historical order book snapshots)

---

### 3. Hybrid Strategies

Strategies can implement multiple callbacks:

```python
class HybridStrategy(BaseStrategy):
    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        """Use price bars in live/backtest contexts"""
        pass

    async def on_order_book(self, snapshot: OrderBookSnapshot, broker: BrokerProtocol) -> None:
        """Use order book in replay context"""
        pass

    async def on_fill(self, side: OrderSide, quantity: int) -> None:
        """Track fills across all contexts"""
        self.position += quantity if side == OrderSide.BUY else -quantity
```

---

## Integration with Existing Code

### Live Trading Context

In `ibkr_trader/strategy.py`, the `Strategy` class extends `BaseStrategy` and adds:
- Event bus subscription for `MARKET_DATA` events
- Automatic conversion of events to `on_bar()` calls
- Position tracking helpers

```python
class Strategy(BaseStrategy):
    """Extended for live trading with event bus integration"""

    def __init__(
        self,
        config: StrategyConfig,
        broker: BrokerProtocol,  # IBKRBroker or SimulatedBroker
        event_bus: EventBus,
        risk_guard: RiskGuard | None = None,
    ):
        self.broker = broker
        self.event_bus = event_bus
        # ... setup

    async def start(self) -> None:
        """Subscribe to MARKET_DATA topic and begin processing"""
        self._subscription = self.event_bus.subscribe(EventTopic.MARKET_DATA)
        self._task = asyncio.create_task(self._run_event_loop())

    async def _run_event_loop(self) -> None:
        """Internal: Convert MarketDataEvent -> on_bar() calls"""
        async for event in self._subscription:
            await self.on_bar(event.symbol, event.price, self.broker)
```

**To create a live strategy:**

```python
from ibkr_trader.strategy import Strategy, StrategyConfig
from ibkr_trader.execution import IBKRBroker
from ibkr_trader.core import EventBus

class MyLiveStrategy(Strategy):
    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        # Your strategy logic here
        pass

# Instantiate for live trading
config = StrategyConfig(name="MyStrategy", symbols=["AAPL"], position_size=10)
broker = IBKRBroker(config=ibkr_config, guard=guard, risk_guard=risk_guard)
event_bus = EventBus()

strategy = MyLiveStrategy(config, broker, event_bus)
await strategy.start()  # Begins processing market data
```

---

### Replay Context

In `ibkr_trader/sim/runner.py`, the `ReplayStrategy` class extends `BaseStrategy`:

```python
class ReplayStrategy(BaseStrategy):
    """Strategy interface for replay simulations"""
    pass  # All methods inherited from BaseStrategy
```

**To create a replay strategy:**

```python
from ibkr_trader.sim.runner import ReplayStrategy
from model.data.models import OrderBookSnapshot

class MyReplayStrategy(ReplayStrategy):
    async def on_order_book(self, snapshot: OrderBookSnapshot, broker: BrokerProtocol) -> None:
        # Your strategy logic here
        pass

# Instantiate for replay
from ibkr_trader.sim.runner import ReplayRunner
from ibkr_trader.sim.events import EventLoader

loader = EventLoader(order_book_files=[...])
strategy = MyReplayStrategy()
runner = ReplayRunner(loader, strategy)

await runner.run()  # Replays historical events
```

---

## Testing Strategies

### Unit Testing

Test strategies independently by mocking the broker:

```python
import pytest
from unittest.mock import AsyncMock
from decimal import Decimal

@pytest.mark.asyncio
async def test_sma_strategy_generates_buy_signal():
    # Arrange
    strategy = SimpleMovingAverageStrategy(fast_period=2, slow_period=3)
    broker = AsyncMock()
    broker.get_positions.return_value = []

    # Act - feed prices that create bullish crossover
    await strategy.on_bar("AAPL", Decimal("1.0"), broker)
    await strategy.on_bar("AAPL", Decimal("2.0"), broker)
    await strategy.on_bar("AAPL", Decimal("3.0"), broker)
    await strategy.on_bar("AAPL", Decimal("4.0"), broker)  # Crossover here

    # Assert
    assert broker.place_order.called
    order_request = broker.place_order.call_args[0][0]
    assert order_request.side == OrderSide.BUY
```

---

### Integration Testing

Test strategies with SimulatedBroker:

```python
from ibkr_trader.sim.broker import SimulatedBroker
from ibkr_trader.core import EventBus

@pytest.mark.asyncio
async def test_strategy_with_simulated_broker():
    event_bus = EventBus()
    broker = SimulatedBroker(event_bus)
    strategy = MyStrategy()

    await strategy.on_bar("AAPL", Decimal("100.0"), broker)

    positions = await broker.get_positions()
    assert len(positions) == 1
    assert positions[0].quantity == 10
```

---

## Migration Guide

### Upgrading Existing Strategies

If you have strategies written before the unified interface:

**Old style (pre-unification):**
```python
class MyStrategy(Strategy):
    async def on_bar(self, symbol: str, price: Decimal) -> None:
        position = await self.get_position(symbol)
        await self.place_market_order(symbol, OrderSide.BUY, 10)
```

**New style (post-unification):**
```python
class MyStrategy(Strategy):
    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        position = await self.get_position(symbol)  # Still works
        # OR: position = await self.get_position(symbol, broker)  # New preferred way

        # Use broker directly for orders
        order = OrderRequest(
            contract=SymbolContract(symbol=symbol),
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.MARKET
        )
        await broker.place_order(order)

        # OR: Use helper if available
        await self.place_market_order(symbol, OrderSide.BUY, 10)
```

**Key changes:**
1. Add `broker: BrokerProtocol` parameter to `on_bar()`
2. Use `broker.place_order()` directly (more explicit, works everywhere)
3. Helper methods like `place_market_order()` still work but are less portable

---

## Best Practices

### 1. Always Use the Broker Parameter

✅ **Good:**
```python
async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
    position = await self.get_position(symbol, broker)
    await broker.place_order(order_request)
```

❌ **Avoid:**
```python
async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
    position = await self.broker.get_positions()  # Assumes self.broker exists
```

**Why:** Strategies that use the broker parameter are context-agnostic and work everywhere.

---

### 2. Implement Only What You Need

✅ **Good:**
```python
class SimplePriceStrategy(BaseStrategy):
    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        # Just implement on_bar
        pass
```

❌ **Avoid:**
```python
class SimplePriceStrategy(BaseStrategy):
    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        pass

    async def on_order_book(self, snapshot: OrderBookSnapshot, broker: BrokerProtocol) -> None:
        pass  # Empty implementation not needed

    async def on_trade(self, trade: TradeEvent, broker: BrokerProtocol) -> None:
        pass  # Empty implementation not needed
```

**Why:** BaseStrategy provides default no-op implementations. Only override what you use.

---

### 3. Use Type Hints

✅ **Good:**
```python
from ibkr_trader.base_strategy import BrokerProtocol

async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
    pass
```

❌ **Avoid:**
```python
async def on_bar(self, symbol, price, broker):  # No type hints
    pass
```

**Why:** Type hints enable mypy checking and IDE autocomplete. Critical for safety.

---

### 4. Handle Position State Carefully

✅ **Good:**
```python
async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
    current_position = await self.get_position(symbol, broker)

    if self.should_buy(price) and current_position <= 0:
        # Only buy if flat or short
        await broker.place_order(...)
```

❌ **Avoid:**
```python
async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
    if self.should_buy(price):
        # Always buy, ignoring current position
        await broker.place_order(...)
```

**Why:** Position-aware logic prevents duplicate orders and pyramiding.

---

## Common Patterns

### Pattern 1: Mean Reversion with Stop Loss

```python
class MeanReversionStrategy(BaseStrategy):
    def __init__(self, symbol: str, lookback: int, entry_zscore: float):
        self.symbol = symbol
        self.lookback = lookback
        self.entry_zscore = entry_zscore
        self.prices = []
        self.entry_price = None

    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        self.prices.append(float(price))

        if len(self.prices) < self.lookback:
            return

        recent = self.prices[-self.lookback:]
        mean = sum(recent) / len(recent)
        std = (sum((x - mean)**2 for x in recent) / len(recent)) ** 0.5
        zscore = (float(price) - mean) / std if std > 0 else 0

        position = await self.get_position(symbol, broker)

        # Stop loss
        if self.entry_price and abs(float(price) - self.entry_price) > 2 * std:
            await self.close_position(broker, position)
            return

        # Entry logic
        if position == 0:
            if zscore <= -self.entry_zscore:
                await self.open_position(broker, OrderSide.BUY)
                self.entry_price = float(price)
            elif zscore >= self.entry_zscore:
                await self.open_position(broker, OrderSide.SELL)
                self.entry_price = float(price)

        # Exit logic
        elif abs(zscore) <= 0.5:
            await self.close_position(broker, position)
```

---

### Pattern 2: ML Model Integration

```python
import pickle
from pathlib import Path

class MLStrategy(BaseStrategy):
    def __init__(self, model_path: Path):
        with open(model_path, 'rb') as f:
            self.model = pickle.load(f)
        self.feature_buffer = []

    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        # Build features
        self.feature_buffer.append(float(price))
        if len(self.feature_buffer) < 20:
            return

        features = self.extract_features(self.feature_buffer[-20:])
        prediction = self.model.predict([features])[0]

        position = await self.get_position(symbol, broker)

        if prediction > 0.6 and position <= 0:
            await self.buy(broker, symbol)
        elif prediction < 0.4 and position >= 0:
            await self.sell(broker, symbol)

    def extract_features(self, prices):
        # Your feature engineering here
        return prices
```

---

### Pattern 3: Multi-Symbol Portfolio Strategy

```python
class PortfolioStrategy(BaseStrategy):
    def __init__(self, symbols: list[str]):
        self.symbols = symbols
        self.prices = {s: [] for s in symbols}

    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        if symbol not in self.symbols:
            return

        self.prices[symbol].append(float(price))

        # Wait for all symbols to have data
        if any(len(self.prices[s]) < 20 for s in self.symbols):
            return

        # Calculate correlations, relative strength, etc.
        # Rebalance portfolio based on signals
        await self.rebalance_portfolio(broker)
```

---

## Troubleshooting

### Issue: Strategy not receiving callbacks

**Symptom:** `on_bar()` never called in live trading

**Solution:** Ensure strategy is started and event bus is publishing:
```python
await strategy.start()  # Critical! Subscribes to event bus
```

---

### Issue: Type errors with broker parameter

**Symptom:** `mypy` complains about broker type

**Solution:** Use `BrokerProtocol` type hint:
```python
from ibkr_trader.base_strategy import BrokerProtocol

async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
    pass
```

---

### Issue: Orders not executing in replay

**Symptom:** `broker.place_order()` called but no fills

**Solution:** MockBroker in replay needs explicit fill handling. Check that:
1. ReplayRunner's `_handle_executions()` is running
2. Event bus is publishing `EXECUTION` events

---

## Next Steps

- **Step 3:** Extend CLI to support `--config` parameter (coming soon)
- **Step 4:** Run advanced strategies live (coming soon)
- **Examples:** Example strategy implementations (coming soon)

---

## Reference

### BaseStrategy API

```python
class BaseStrategy(ABC):
    # Callbacks (all optional)
    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None
    async def on_order_book(self, snapshot: OrderBookSnapshot, broker: BrokerProtocol) -> None
    async def on_trade(self, trade: TradeEvent, broker: BrokerProtocol) -> None
    async def on_option_surface(self, entry: OptionSurfaceEntry, broker: BrokerProtocol) -> None
    async def on_fill(self, side: OrderSide, quantity: int) -> None

    # Lifecycle
    async def start(self) -> None
    async def stop(self) -> None

    # Utilities
    async def get_position(self, symbol: str, broker: BrokerProtocol) -> int
```

### BrokerProtocol API

```python
class BrokerProtocol(Protocol):
    async def place_order(self, request: OrderRequest) -> OrderResult
    async def get_positions(self) -> list[Position]
```

---

*Last updated: 2025-10-17 (Phase 1 Step 2 completion)*
