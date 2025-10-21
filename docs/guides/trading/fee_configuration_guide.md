# Fee and Slippage Configuration Guide

**Version**: 1.0
**Last Updated**: 2025-10-21
**Status**: Production-ready

---

## Overview

The IBKR Personal Trader platform includes comprehensive fee and slippage estimation to help you understand the true cost of trading. This guide covers how to configure, enable, and use fee-aware risk management.

## Table of Contents

1. [What Are Transaction Costs?](#what-are-transaction-costs)
2. [Default Fee Profiles](#default-fee-profiles)
3. [Enabling Fee Estimates](#enabling-fee-estimates)
4. [Configuration Reference](#configuration-reference)
5. [Using Fee-Aware Risk Guards](#using-fee-aware-risk-guards)
6. [Custom Fee Profiles](#custom-fee-profiles)
7. [Viewing Fee Estimates](#viewing-fee-estimates)
8. [Best Practices](#best-practices)

---

## What Are Transaction Costs?

Every trade incurs two primary costs:

### 1. Commission
Direct fees charged by your broker (IBKR) for executing trades.

**Examples**:
- **US Stocks**: $0.005/share, minimum $1.00 per order
- **Forex**: 0.2 basis points (0.00002) of notional value
- **Options**: $0.65 per contract, minimum $1.00 per order

### 2. Slippage
The difference between expected and actual execution prices due to market impact and spread costs.

**Examples**:
- **Liquid stocks**: ~5 basis points (0.05%)
- **Forex majors**: ~1 basis point (0.01%)
- **Options**: ~20 basis points (0.20%)

---

## Default Fee Profiles

The platform ships with **IBKR-accurate default profiles** based on IBKR Tiered pricing:

| Asset Class | Commission | Slippage Estimate |
|-------------|------------|-------------------|
| **US Stocks** | $0.005/share, min $1.00 | 5 bps (0.05%) |
| **Forex** | 0.2 bps of notional | 1 bp (0.01%) |
| **Options** | $0.65/contract, min $1.00 | 20 bps (0.20%) |
| **Futures** | $0.85/contract | 5 bps (0.05%) |

### Example Cost Calculations

**100 shares of AAPL at $150**:
- Notional: $15,000
- Commission: max($0.005 × 100, $1.00) = **$1.00**
- Slippage: $15,000 × 0.0005 = **$7.50**
- **Total cost**: $8.50

**10,000 EUR/USD at 1.10**:
- Notional: $11,000
- Commission: $11,000 × 0.00002 = **$0.22**
- Slippage: $11,000 × 0.0001 = **$1.10**
- **Total cost**: $1.32

---

## Enabling Fee Estimates

Fee estimation is **disabled by default** to maintain backward compatibility.

### Method 1: Environment Variables

Add to your `.env` file:

```bash
# Enable fee and slippage estimates
IBKR_ENABLE_FEE_ESTIMATES=true
```

### Method 2: Programmatic Configuration

```python
from ibkr_trader.config import IBKRConfig

config = IBKRConfig(enable_fee_estimates=True)
```

### Method 3: CLI Flag (when available)

```bash
ibkr-trader run --enable-fees
```

---

## Configuration Reference

### Core Settings

```bash
# Enable/disable fee estimates (default: false)
IBKR_ENABLE_FEE_ESTIMATES=true
```

### Stock Trading

```bash
# Commission per share (default: 0.005 = $0.005/share)
IBKR_STOCK_COMMISSION_PER_SHARE=0.005

# Minimum commission per order (default: 1.00)
IBKR_STOCK_COMMISSION_MINIMUM=1.00

# Slippage in basis points (default: 5.0 = 0.05%)
IBKR_STOCK_SLIPPAGE_BPS=5.0
```

### Forex Trading

```bash
# Commission as percentage of notional (default: 0.00002 = 0.2 bps)
IBKR_FOREX_COMMISSION_PERCENTAGE=0.00002

# Slippage in basis points (default: 1.0 = 0.01%)
IBKR_FOREX_SLIPPAGE_BPS=1.0
```

### Option Trading

```bash
# Commission per contract (default: 0.65)
IBKR_OPTION_COMMISSION_PER_CONTRACT=0.65

# Slippage in basis points (default: 20.0 = 0.20%)
IBKR_OPTION_SLIPPAGE_BPS=20.0
```

---

## Using Fee-Aware Risk Guards

When fee estimates are enabled, `RiskGuard` automatically includes transaction costs in exposure calculations.

### Setup

```python
from decimal import Decimal
from ibkr_trader.config import IBKRConfig
from ibkr_trader.risk import PortfolioState, RiskGuard

# Load config with fee estimates enabled
config = IBKRConfig(enable_fee_estimates=True)

# Create portfolio state
portfolio = PortfolioState(
    max_daily_loss=Decimal("1000"),
    snapshot_path=Path("data/portfolio_snapshot.json"),
)

# Create fee-aware risk guard
fee_config = config.create_fee_config()
risk_guard = RiskGuard(
    portfolio=portfolio,
    max_exposure=Decimal("10000"),
    fee_config=fee_config,  # Enable fee-aware validation
)
```

### Behavior

With fees enabled, order validation includes transaction costs:

```python
# Order: 100 shares at $50
# Base exposure: $5,000
# Commission: $1.00
# Slippage: $2.50
# Total exposure: $5,003.50

await risk_guard.validate_order(
    contract=SymbolContract(symbol="AAPL"),
    side=OrderSide.BUY,
    quantity=100,
    price=Decimal("50"),
)
# ✅ Passes if fee-adjusted exposure < max_exposure
# ❌ Raises RuntimeError if fee-adjusted exposure > max_exposure
```

**Without fees**: Only checks $5,000 vs limit
**With fees**: Checks $5,003.50 vs limit

---

## Custom Fee Profiles

### Scenario: Higher Commission Broker

If you're using a different pricing tier or broker:

```bash
# Higher per-share commission
IBKR_STOCK_COMMISSION_PER_SHARE=0.01

# Higher minimum
IBKR_STOCK_COMMISSION_MINIMUM=5.00
```

### Scenario: Illiquid Markets

For less liquid assets, increase slippage estimates:

```bash
# Higher slippage for illiquid stocks
IBKR_STOCK_SLIPPAGE_BPS=20.0  # 0.20% instead of 0.05%
```

### Scenario: Institutional Pricing

For volume discounts or institutional rates:

```bash
# Lower commission with volume pricing
IBKR_STOCK_COMMISSION_PER_SHARE=0.0025
IBKR_STOCK_COMMISSION_MINIMUM=0.50

# Tighter slippage with better execution
IBKR_STOCK_SLIPPAGE_BPS=3.0
```

### Programmatic Customization

```python
from decimal import Decimal
from ibkr_trader.risk.fees import CommissionProfile, FeeConfig, SlippageEstimate

# Create custom fee configuration
custom_fees = FeeConfig(
    stock_commission=CommissionProfile(
        per_share=Decimal("0.01"),  # Higher than default
        minimum=Decimal("5.00"),
    ),
    stock_slippage=SlippageEstimate(
        basis_points=Decimal("10.0")  # Higher slippage
    ),
)

# Use in RiskGuard
risk_guard = RiskGuard(
    portfolio=portfolio,
    max_exposure=Decimal("10000"),
    fee_config=custom_fees,
)
```

---

## Viewing Fee Estimates

### Log Output

When placing orders with fee-aware RiskGuard, estimated costs are logged:

```
INFO: Estimated costs for AAPL: commission=$1.00, slippage=$7.50, total=$8.50
INFO: Fee-aware exposure for AAPL: base=5000 costs=8.50 total=5008.50
INFO: Placing order: BUY 100 AAPL @ MARKET
```

### Calculating Costs Manually

```python
from decimal import Decimal
from ibkr_trader.config import IBKRConfig
from ibkr_trader.models import OrderSide, SymbolContract

config = IBKRConfig()
fee_config = config.create_fee_config()

# Estimate costs for a specific order
contract = SymbolContract(symbol="AAPL", sec_type="STK")
commission, slippage = fee_config.estimate_costs(
    contract=contract,
    side=OrderSide.BUY,
    quantity=100,
    price=Decimal("150"),
)

print(f"Commission: ${commission}")
print(f"Slippage: ${slippage}")
print(f"Total: ${commission + slippage}")
```

**Output**:
```
Commission: $1.00
Slippage: $7.50
Total: $8.50
```

---

## Best Practices

### 1. Enable Fees for Production

Always enable fee estimates when running live strategies:

```bash
IBKR_ENABLE_FEE_ESTIMATES=true
```

**Why**: Fee-adjusted exposure limits prevent you from inadvertently exceeding risk limits due to transaction costs.

### 2. Calibrate Slippage Estimates

Default estimates are conservative. Adjust based on your actual slippage:

```bash
# If you consistently see better execution
IBKR_STOCK_SLIPPAGE_BPS=3.0

# If trading illiquid assets
IBKR_STOCK_SLIPPAGE_BPS=15.0
```

### 3. Review Commission Tier

Ensure your commission settings match your IBKR pricing tier:

- **Tiered**: `IBKR_STOCK_COMMISSION_PER_SHARE=0.005` ✅ (default)
- **Fixed**: `IBKR_STOCK_COMMISSION_PER_SHARE=0.01`

### 4. Test Without Fees First

When developing new strategies, test without fees first:

```python
# Development: no fees
risk_guard = RiskGuard(portfolio, max_exposure, fee_config=None)

# Production: with fees
risk_guard = RiskGuard(portfolio, max_exposure, fee_config=fee_config)
```

### 5. Monitor Actual vs Estimated Costs

Track actual execution costs and compare to estimates:

```python
# Actual commission from execution event
actual_commission = execution_event.commission

# Compare to estimate
estimated_commission, estimated_slippage = fee_config.estimate_costs(...)
```

### 6. Use Conservative Estimates

It's better to overestimate costs than underestimate:

```bash
# Conservative slippage for safety
IBKR_STOCK_SLIPPAGE_BPS=7.0  # Slightly higher than default 5.0
```

---

## Architecture

### Fee Calculation Flow

```
Order Request
    ↓
RiskGuard.validate_order()
    ↓
fee_config.estimate_costs() ← Uses asset-specific profiles
    ↓
Calculate fee-adjusted exposure = base + commission + slippage
    ↓
Compare to max_exposure limit
    ↓
✅ Pass / ❌ Raise RuntimeError
```

### Components

**`FeeConfig`** (`ibkr_trader/risk/fees.py`)
- Manages commission and slippage profiles
- Methods: `estimate_costs()`, `total_cost()`

**`CommissionProfile`**
- Per-share, percentage, minimum, maximum
- Asset-class specific (stocks, FX, options, futures)

**`SlippageEstimate`**
- Basis points or fixed amount per share
- Asset-class specific

**`RiskGuard`** (`ibkr_trader/risk/portfolio.py`)
- Optional `fee_config` parameter
- Automatically uses fee-adjusted exposure when configured

**`IBKRConfig`** (`ibkr_trader/core/config.py`)
- Environment variable support for all fee settings
- Method: `create_fee_config()` to generate FeeConfig

---

## Troubleshooting

### Issue: Orders Rejected Despite Being Under Limit

**Symptom**: Order rejected with "exceeds max exposure" even though notional is under limit.

**Cause**: Fee-adjusted exposure exceeds limit.

**Solution**: Increase `max_exposure` or reduce order size to account for fees.

```python
# Before: max_exposure = 5000, order = 100 @ $50 = $5000
# After fees: $5008.50 > $5000 ❌

# Fix 1: Increase limit
max_exposure = Decimal("5050")

# Fix 2: Reduce order size
quantity = 99  # 99 @ $50 = $4950 + fees = $4958.41 ✅
```

### Issue: Fees Seem Too High

**Symptom**: Estimated costs are higher than expected.

**Cause**: Default slippage estimates are conservative.

**Solution**: Calibrate slippage based on your execution data.

```bash
# If you see lower slippage in practice
IBKR_STOCK_SLIPPAGE_BPS=3.0  # Down from default 5.0
```

### Issue: Different Costs for Same Order

**Symptom**: Same order shows different costs at different times.

**Cause**: Fee estimates are static; actual costs vary with market conditions.

**Solution**: This is expected. Estimates are for planning; actual costs depend on execution quality.

---

## Examples

### Example 1: Fee-Aware Strategy Setup

```python
from decimal import Decimal
from pathlib import Path
from ibkr_trader.config import IBKRConfig
from ibkr_trader.broker import IBKRBroker
from ibkr_trader.risk import PortfolioState, RiskGuard
from ibkr_trader.safety import LiveTradingGuard

# Load config with fees enabled
config = IBKRConfig(enable_fee_estimates=True)

# Create components
guard = LiveTradingGuard(config=config)
portfolio = PortfolioState(
    max_daily_loss=Decimal(str(config.max_daily_loss)),
    snapshot_path=Path("data/portfolio_snapshot.json"),
)

# Create fee-aware risk guard
fee_config = config.create_fee_config()
risk_guard = RiskGuard(
    portfolio=portfolio,
    max_exposure=Decimal(str(config.max_order_exposure)),
    fee_config=fee_config,
)

# Create broker with fee-aware risk guard
broker = IBKRBroker(
    config=config,
    guard=guard,
    risk_guard=risk_guard,
)
```

### Example 2: Cost Comparison Tool

```python
from decimal import Decimal
from ibkr_trader.config import IBKRConfig
from ibkr_trader.models import OrderSide, SymbolContract

def compare_execution_costs(symbol: str, quantity: int, price: Decimal) -> None:
    """Compare costs across different fee profiles."""
    config = IBKRConfig()
    fee_config = config.create_fee_config()

    contract = SymbolContract(symbol=symbol, sec_type="STK")

    commission, slippage = fee_config.estimate_costs(
        contract=contract,
        side=OrderSide.BUY,
        quantity=quantity,
        price=price,
    )

    notional = quantity * price
    total_cost = commission + slippage
    cost_pct = (total_cost / notional) * 100

    print(f"\n{symbol}: {quantity} shares @ ${price}")
    print(f"Notional: ${notional:,.2f}")
    print(f"Commission: ${commission}")
    print(f"Slippage: ${slippage}")
    print(f"Total Cost: ${total_cost} ({cost_pct:.3f}%)")

# Compare different order sizes
compare_execution_costs("AAPL", 100, Decimal("150"))
compare_execution_costs("AAPL", 1000, Decimal("150"))
compare_execution_costs("AAPL", 10000, Decimal("150"))
```

### Example 3: Fee Impact on Returns

```python
def calculate_required_return(cost: Decimal, notional: Decimal) -> Decimal:
    """Calculate minimum return needed to cover transaction costs."""
    return (cost / notional) * 100

# Round-trip costs (entry + exit)
entry_cost = Decimal("8.50")
exit_cost = Decimal("8.50")
total_cost = entry_cost + exit_cost
notional = Decimal("15000")

min_return = calculate_required_return(total_cost, notional)
print(f"Minimum return to break even: {min_return:.3f}%")
# Output: Minimum return to break even: 0.113%
```

---

## See Also

- [Risk Management Guide](../operations/risk_management_guide.md)
- [Strategy Development Guide](../strategies/unified_strategy_guide.md)
- [Configuration Reference](../../README.md#configuration)
- [IBKR Pricing Tiers](https://www.interactivebrokers.com/en/pricing/commissions-stocks.php)

---

**Last Updated**: 2025-10-21
**Maintained By**: IBKR Personal Trader Team
