# Trailing Stops Guide

## Overview

Trailing stops automatically adjust stop loss orders as the market price moves in your favor, allowing you to lock in profits while giving the position room to grow. The stop never widens (moves against your position).

### How It Works

**For Long Positions (SELL stop):**
- When price increases, the stop loss rises
- When price decreases, the stop loss stays where it is
- Result: Stop follows price up, locking in gains

**For Short Positions (BUY stop):**
- When price decreases, the stop loss lowers
- When price increases, the stop loss stays where it is
- Result: Stop follows price down, locking in gains

### Key Features

- **Automatic Adjustment**: Stop updates as price moves favorably
- **Never Widens**: Stop never moves against your position
- **Rate Limited**: Max 1 update per second per symbol (IBKR compliance)
- **Activation Threshold**: Optional - only start trailing above/below a price
- **State Persistence**: Survives process restarts
- **Flexible Configuration**: Dollar amount OR percentage trailing

## Using Trailing Stops

### CLI Command

```bash
ibkr-trader trailing-stop \
  --symbol AAPL \
  --side SELL \
  --quantity 10 \
  --trail-amount 5.00 \
  --initial-price 150.00
```

### Parameters

- `--symbol` (required): Trading symbol
- `--side` (required): SELL for long position, BUY for short position
- `--quantity` (required): Position quantity
- `--initial-price` (required): Current market price for calculating initial stop
- `--trail-amount` OR `--trail-percent` (required, mutually exclusive):
  - `--trail-amount`: Dollar amount to trail (e.g., 5.00)
  - `--trail-percent`: Percentage to trail (e.g., 2.0 for 2%)
- `--activation-price` (optional): Start trailing only above/below this price

### Examples

#### Long Position with Dollar Amount Trailing

```bash
# Buy 10 shares of AAPL at 150
# Trail by $5 - stop starts at 145, rises with price

ibkr-trader trailing-stop \
  --symbol AAPL \
  --side SELL \
  --quantity 10 \
  --trail-amount 5.00 \
  --initial-price 150.00
```

**Price Movement:**
- Price at 150 → Stop at 145
- Price rises to 155 → Stop rises to 150
- Price rises to 160 → Stop rises to 155
- Price falls to 156 → Stop stays at 155 ✅ (never widens)

#### Short Position with Percentage Trailing

```bash
# Short 10 shares of AAPL at 150
# Trail by 2% - stop starts at 153, lowers with price

ibkr-trader trailing-stop \
  --symbol AAPL \
  --side BUY \
  --quantity 10 \
  --trail-percent 2.0 \
  --initial-price 150.00
```

**Price Movement:**
- Price at 150 → Stop at 153 (150 * 1.02)
- Price falls to 145 → Stop lowers to 147.90 (145 * 1.02)
- Price falls to 140 → Stop lowers to 142.80 (140 * 1.02)
- Price rises to 142 → Stop stays at 142.80 ✅ (never widens)

#### With Activation Threshold

```bash
# Long position: only start trailing after price reaches 155

ibkr-trader trailing-stop \
  --symbol AAPL \
  --side SELL \
  --quantity 10 \
  --trail-amount 5.00 \
  --initial-price 150.00 \
  --activation-price 155.00
```

**Price Movement:**
- Price at 150 → Stop at 145, NOT ACTIVE yet
- Price at 152 → Stop at 145, still not active
- Price reaches 156 → ACTIVATES, stop now at 151
- Price rises to 160 → Stop rises to 155
- Price falls to 157 → Stop stays at 155 ✅

## Programmatic Usage

### Using TrailingStopManager

```python
import asyncio
from decimal import Decimal
from pathlib import Path

from ibkr_trader.broker import IBKRBroker
from ibkr_trader.config import load_config
from ibkr_trader.events import EventBus
from ibkr_trader.models import OrderSide, TrailingStopConfig
from ibkr_trader.safety import LiveTradingGuard
from ibkr_trader.trailing_stops import TrailingStopManager

async def create_trailing_stop_example():
    config = load_config()
    guard = LiveTradingGuard(config=config, live_flag_enabled=False)
    guard.acknowledge_live_trading()

    event_bus = EventBus()
    broker = IBKRBroker(config=config, guard=guard, event_bus=event_bus)

    state_file = config.data_dir / "trailing_stops.json"
    trailing_manager = TrailingStopManager(
        broker=broker,
        event_bus=event_bus,
        state_file=state_file,
    )

    await broker.connect()
    await trailing_manager.start()

    try:
        # Create trailing stop configuration
        trailing_config = TrailingStopConfig(
            symbol="AAPL",
            side=OrderSide.SELL,  # Long position
            quantity=10,
            trail_amount=Decimal("5.00"),
            activation_price=Decimal("155.00"),  # Optional
        )

        # Get current price (from broker or market data)
        initial_price = Decimal("150.00")

        # Create the trailing stop
        stop_id = await trailing_manager.create_trailing_stop(
            trailing_config,
            initial_price
        )

        print(f"Trailing stop created: {stop_id}")

        # Manager will automatically monitor and adjust
        # Keep event loop running for continuous monitoring
        await asyncio.sleep(3600)  # Monitor for 1 hour

    finally:
        await trailing_manager.stop()
        await broker.disconnect()

# Run the example
asyncio.run(create_trailing_stop_example())
```

### Integration with Strategies

```python
from ibkr_trader.base_strategy import BaseStrategy
from ibkr_trader.broker_protocol import BrokerProtocol
from ibkr_trader.models import OrderSide
from ibkr_trader.trailing_stops import TrailingStopManager
from decimal import Decimal

class MyStrategyWithTrailing(BaseStrategy):
    def __init__(self, trailing_manager: TrailingStopManager):
        self.trailing_manager = trailing_manager

    async def on_bar(self, broker: BrokerProtocol, symbol: str, price: Decimal) -> None:
        # Example: Enter position and immediately set trailing stop
        if self.should_enter_long(symbol, price):
            # Place order
            await broker.place_market_order(
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=10
            )

            # Set trailing stop
            trailing_config = TrailingStopConfig(
                symbol=symbol,
                side=OrderSide.SELL,  # Exit side for long
                quantity=10,
                trail_percent=Decimal("2.0"),  # 2% trailing
            )

            await self.trailing_manager.create_trailing_stop(
                trailing_config,
                price
            )
```

## Best Practices

### 1. Choose Appropriate Trail Distance

**Too Tight:**
- Pros: Locks in profits quickly
- Cons: May get stopped out prematurely on normal volatility

**Too Loose:**
- Pros: Gives position room to breathe
- Cons: May give back more profit before stop triggers

**Recommendation:**
- Volatile stocks (NVDA, TSLA): 3-5% or $10-15
- Stable stocks (AAPL, MSFT): 2-3% or $5-8
- Use ATR (Average True Range) for dynamic sizing

### 2. Use Activation Thresholds

For breakout trades, wait for confirmation before trailing:

```bash
# Only start trailing after price breaks above resistance
ibkr-trader trailing-stop \
  --symbol AAPL \
  --side SELL \
  --quantity 10 \
  --trail-amount 5.00 \
  --initial-price 148.00 \
  --activation-price 152.00  # Resistance level
```

### 3. Dollar Amount vs Percentage

**Dollar Amount:**
- Use for: Similar-priced stocks, simpler math
- Example: $5 trail on $150 stock = 3.33%

**Percentage:**
- Use for: Stocks at different price points, scaling with volatility
- Example: 2% trail works for $50 stock ($1) and $500 stock ($10)

### 4. Monitor Active Trailing Stops

The trailing stop state is persisted in `data/trailing_stops.json`:

```bash
# View active trailing stops
cat data/trailing_stops.json | jq
```

### 5. Combine with Time-Based Exits

Trailing stops protect profits, but don't protect against time decay:

```python
# Strategy: Exit after 30 days OR trailing stop hit
entry_time = datetime.now()
days_held = (datetime.now() - entry_time).days

if days_held >= 30:
    await self.exit_position(symbol)
# Otherwise, trailing stop will handle exit
```

## Validation Rules

The `TrailingStopConfig` model enforces:

1. **Exactly one trailing method**: Must specify either `trail_amount` OR `trail_percent`, not both
2. **Positive trail amount**: If using dollar amount, must be > 0
3. **Valid trail percent**: If using percentage, must be between 0 and 100
4. **Positive quantity**: Position quantity must be > 0
5. **Valid symbol**: Symbol cannot be empty, automatically uppercased

## How It Works Internally

### IBKR Integration

1. **Initial Stop Order**: TrailingStopManager places a regular STOP order at the initial stop price
2. **Market Data Subscription**: Manager subscribes to market data events for the symbol
3. **Price Updates**: On each price update:
   - Check if price moved favorably (up for long, down for short)
   - Calculate new stop price
   - If new stop is better, modify the order (rate limited)
4. **Order Modification**: Uses IBKR's order modification API to adjust stop price
5. **Stop Triggers**: When market hits stop price, IBKR executes the stop order normally

### State Persistence

Active trailing stops are saved to `data/trailing_stops.json`:

```json
{
  "stops": [
    {
      "stop_id": "AAPL_1001",
      "order_id": 1001,
      "current_stop_price": "150.00",
      "high_water_mark": "160.00",
      "activated": true,
      "last_update_time": "2025-10-20T12:00:00",
      "config": {
        "symbol": "AAPL",
        "side": "SELL",
        "quantity": 10,
        "trail_amount": "5.00",
        "trail_percent": null,
        "activation_price": null
      }
    }
  ]
}
```

This allows the manager to resume monitoring after restarts.

### Rate Limiting

To comply with IBKR's order modification limits:
- Maximum 1 modification per second per symbol
- Rapid price changes are queued
- Last price update wins (intermediate updates skipped)

## Troubleshooting

### "Must specify either --trail-amount or --trail-percent"

**Problem**: Didn't provide trailing method.

**Solution**: Specify exactly one:
```bash
--trail-amount 5.00    # Dollar amount
# OR
--trail-percent 2.0    # Percentage
```

### "Cannot specify both trail_amount and trail_percent"

**Problem**: Provided both trailing methods.

**Solution**: Choose one or the other, not both.

### "Trailing stop created but not monitored after command exits"

**Problem**: The CLI command creates the stop but doesn't monitor it.

**Solution**: Integrate TrailingStopManager into your trading strategy:
```python
# In your strategy's start() method
await self.trailing_manager.start()

# In your strategy's stop() method
await self.trailing_manager.stop()
```

### Stop Not Updating

**Possible causes:**
1. **Rate limiting**: Updates limited to 1/sec per symbol
2. **Price not favorable**: Stop only moves when price improves
3. **Not activated**: Check if activation threshold reached
4. **Event bus not running**: Ensure strategy/manager is running

**Debug:**
```python
# Check active stops
print(f"Active stops: {trailing_manager.active_stops}")

# Check specific stop
stop = trailing_manager.active_stops["AAPL_1001"]
print(f"Current stop: {stop.current_stop_price}")
print(f"High water mark: {stop.high_water_mark}")
print(f"Activated: {stop.activated}")
```

## Paper Trading Only

**IMPORTANT**: The `trailing-stop` CLI command is restricted to PAPER trading mode only.

```bash
# Must have in .env
IBKR_TRADING_MODE=paper

# Attempting to use in live mode will be rejected
```

For live trading, integrate TrailingStopManager into your strategy with appropriate safety checks.

## Testing

Run trailing stop tests:

```bash
# All trailing stop tests
uv run pytest tests/test_trailing_stops.py

# Specific test
uv run pytest tests/test_trailing_stops.py::test_trailing_stop_long_position_raises_with_price_increase -v
```

## See Also

- [Bracket Orders Guide](bracket_orders_guide.md) - Entry + stop loss + take profit
- [Strategy Quick Start](strategy_quick_start.md) - Building custom strategies
- [README](../README.md) - Full platform documentation
- [Trailing Stop Manager](../ibkr_trader/trailing_stops.py) - Implementation details
