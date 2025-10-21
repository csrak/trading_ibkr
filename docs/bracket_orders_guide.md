# Bracket Orders Guide

## Overview

Bracket orders allow you to enter a position with automatic risk management by placing three linked orders simultaneously:

1. **Parent Order (Entry)**: The initial order to enter a position
2. **Stop Loss**: Automatic exit if price moves against you
3. **Take Profit**: Automatic exit to lock in profits

When the parent order fills, both child orders are activated. If either child order fills, the other is automatically cancelled (One-Cancels-Other or OCO).

## Benefits

- **Automatic Risk Management**: Stop loss is placed immediately when you enter
- **Profit Protection**: Take profit order locks in gains automatically
- **Reduced Emotional Trading**: Exits are predetermined before entry
- **One Command**: All three orders placed atomically

## Using Bracket Orders

### CLI Command

The `bracket-order` command is available for paper trading:

```bash
ibkr-trader bracket-order --symbol AAPL --side BUY --quantity 10 \
  --stop-loss 145.00 --take-profit 155.00
```

### Parameters

- `--symbol` (required): Symbol to trade
- `--side` (required): BUY or SELL (default: BUY)
- `--quantity` (required): Number of shares (default: 1)
- `--entry-type`: Entry order type - MARKET or LIMIT (default: MARKET)
- `--entry-limit`: Entry limit price (required if entry-type is LIMIT)
- `--stop-loss` (required): Stop loss price
- `--take-profit` (required): Take profit price
- `--preview`: Preview the order without submitting

### Examples

#### Long Position with Market Entry

```bash
# Buy 10 shares of AAPL at market
# Stop loss at $145, take profit at $155
ibkr-trader bracket-order \
  --symbol AAPL \
  --side BUY \
  --quantity 10 \
  --stop-loss 145.00 \
  --take-profit 155.00
```

#### Short Position with Market Entry

```bash
# Sell short 10 shares of AAPL at market
# Stop loss at $155, take profit at $145
ibkr-trader bracket-order \
  --symbol AAPL \
  --side SELL \
  --quantity 10 \
  --stop-loss 155.00 \
  --take-profit 145.00
```

#### Long Position with Limit Entry

```bash
# Buy 10 shares of AAPL at limit price $150
# Stop loss at $145, take profit at $155
ibkr-trader bracket-order \
  --symbol AAPL \
  --side BUY \
  --quantity 10 \
  --entry-type LIMIT \
  --entry-limit 150.00 \
  --stop-loss 145.00 \
  --take-profit 155.00
```

#### Preview Before Submitting

```bash
# Preview the bracket order without placing it
ibkr-trader bracket-order \
  --symbol AAPL \
  --side BUY \
  --quantity 10 \
  --stop-loss 145.00 \
  --take-profit 155.00 \
  --preview
```

## Programmatic Usage

### Using the Broker API

```python
import asyncio
from decimal import Decimal

from ibkr_trader.core import load_config
from ibkr_trader.execution import IBKRBroker
from ibkr_trader.models import (
    BracketOrderRequest,
    OrderRequest,
    OrderSide,
    OrderType,
    SymbolContract,
)
from ibkr_trader.safety import LiveTradingGuard

async def place_bracket_order_example():
    config = load_config()
    guard = LiveTradingGuard(config=config, live_flag_enabled=False)
    guard.acknowledge_live_trading()

    broker = IBKRBroker(config=config, guard=guard)
    await broker.connect()

    try:
        # Create parent order (entry)
        parent = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.MARKET,
        )

        # Create stop loss order
        stop_loss = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,  # Opposite of parent
            quantity=10,
            order_type=OrderType.STOP,
            stop_price=Decimal("145.00"),
            expected_price=Decimal("145.00"),
        )

        # Create take profit order
        take_profit = OrderRequest(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.SELL,  # Opposite of parent
            quantity=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("155.00"),
            expected_price=Decimal("155.00"),
        )

        # Create bracket order request
        bracket_request = BracketOrderRequest(
            parent=parent,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        # Place the bracket order
        result = await broker.place_bracket_order(bracket_request)

        print(f"Bracket order placed successfully!")
        print(f"Parent order ID: {result.order_id}")
        print(f"Child order IDs: {result.child_order_ids}")

    finally:
        await broker.disconnect()

# Run the example
asyncio.run(place_bracket_order_example())
```

## Validation Rules

The `BracketOrderRequest` model enforces the following validation rules:

1. **Stop Loss Side**: Must be opposite of parent order side
   - If parent is BUY, stop loss must be SELL
   - If parent is SELL, stop loss must be BUY

2. **Take Profit Side**: Must be opposite of parent order side
   - If parent is BUY, take profit must be SELL
   - If parent is SELL, take profit must be BUY

3. **Quantity Matching**: All three orders must have the same quantity
   - Stop loss quantity must match parent quantity
   - Take profit quantity must match parent quantity

4. **Order Types**:
   - Parent: Can be MARKET or LIMIT
   - Stop Loss: Must be STOP or STOP_LIMIT
   - Take Profit: Must be LIMIT

## How It Works

### IBKR Implementation

Bracket orders are implemented using IBKR's parent-child order relationships:

1. **Parent Order** is placed with `transmit=False` (held on server)
2. **Stop Loss** is placed as a child with `parentId` linking to parent
3. **Take Profit** is placed as a child with `parentId` and `transmit=True`
4. All three orders are transmitted atomically when the final child is placed

### Order Lifecycle

```
1. Place parent order → Status: PendingSubmit
2. Place stop loss → Status: PendingSubmit (linked to parent)
3. Place take profit → Status: PendingSubmit (linked to parent)
4. All orders transmitted atomically
5. Parent fills → Both children activate (OCO relationship)
6. Either child fills → Other child is automatically cancelled
```

### OCO (One-Cancels-Other)

The stop loss and take profit orders have an OCO relationship:

- When the parent order fills, both children become active
- If the stop loss fills, the take profit is automatically cancelled
- If the take profit fills, the stop loss is automatically cancelled
- This ensures you exit the position only once

## Best Practices

### 1. Calculate Risk-Reward Ratio

Before placing a bracket order, calculate your risk-reward ratio:

```python
from decimal import Decimal

entry_price = Decimal("150.00")
stop_loss = Decimal("145.00")
take_profit = Decimal("155.00")

risk = entry_price - stop_loss  # 5.00
reward = take_profit - entry_price  # 5.00
risk_reward_ratio = reward / risk  # 1.0 (1:1)

# Aim for at least 1.5:1 or 2:1 risk-reward ratio
```

### 2. Use Appropriate Stop Loss Placement

- **Support/Resistance**: Place stops below support (long) or above resistance (short)
- **Volatility**: Account for normal price fluctuations (ATR-based stops)
- **Max Loss**: Never risk more than 1-2% of account value per trade

### 3. Use Limit Entry When Possible

Limit entry orders give you better control over entry price:

```bash
ibkr-trader bracket-order \
  --symbol AAPL \
  --side BUY \
  --quantity 10 \
  --entry-type LIMIT \
  --entry-limit 149.50 \  # Buy at or below this price
  --stop-loss 145.00 \
  --take-profit 155.00
```

### 4. Preview First

Always preview bracket orders before submitting:

```bash
ibkr-trader bracket-order \
  --symbol AAPL \
  --side BUY \
  --quantity 10 \
  --stop-loss 145.00 \
  --take-profit 155.00 \
  --preview
```

### 5. Monitor Order Status

After placing a bracket order, monitor the status of all three orders:

```bash
# Check account status and positions
ibkr-trader status

# View session telemetry
ibkr-trader session-status
```

## Troubleshooting

### "Stop loss must be opposite side from parent"

**Problem**: Stop loss order has the same side as parent order.

**Solution**: If parent is BUY, stop loss must be SELL (and vice versa).

```bash
# WRONG
ibkr-trader bracket-order --symbol AAPL --side BUY \
  --quantity 10 --stop-loss 145.00 --take-profit 155.00  # Implicit SELL sides - correct!

# The CLI automatically sets child sides correctly
```

### "Stop loss must be STOP or STOP_LIMIT order type"

**Problem**: The stop loss validation ensures you're using the correct order type.

**Solution**: This is handled automatically by the CLI. In programmatic usage, ensure `order_type=OrderType.STOP` or `order_type=OrderType.STOP_LIMIT`.

### "Quantity mismatch"

**Problem**: Parent, stop loss, and take profit have different quantities.

**Solution**: Ensure all three orders have the same quantity.

### Paper Trading Only

**Important**: The `bracket-order` CLI command is restricted to PAPER trading mode only:

```bash
# Must have IBKR_TRADING_MODE=paper in .env
# Attempting to use in live mode will be rejected
```

For live trading, you would need to create a separate live trading command with appropriate safety checks.

## Testing

Run the bracket order tests to verify functionality:

```bash
# Quiet mode (default)
uv run pytest tests/test_bracket_orders.py

# Verbose mode (for debugging)
uv run pytest tests/test_bracket_orders.py -v
```

## See Also

- [Quick Start Guide](../QUICKSTART.md) - Getting started with the platform
- [README](../README.md) - Full platform documentation
- [Broker API](../ibkr_trader/execution/broker.py) - Broker implementation details
- [Models](../ibkr_trader/models.py) - Order request models and validation
