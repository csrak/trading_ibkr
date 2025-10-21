# Trading Examples - Full Workflow Guide

This directory contains ready-to-run examples for the complete trading lifecycle:

**Train → Backtest → Paper Trade → Live Trade**

## Directory Structure

```
examples/
├── README.md                 # This file
├── strategies/              # Ready-to-use strategy implementations
│   ├── sma_example.py       # Simple Moving Average strategy
│   ├── mean_reversion.py    # Mean reversion strategy
│   ├── industry_model.py    # ML-based industry model strategy
│   └── market_maker.py      # Basic market making strategy
├── configs/                 # Strategy configuration files
│   ├── mean_reversion.json  # Mean reversion config
│   ├── vol_overlay.json     # Volatility overlay config
│   └── fixed_spread_mm.json # Market maker config
├── scripts/                 # End-to-end workflow scripts
│   ├── 01_train_model.sh    # Train industry model
│   ├── 02_backtest.sh       # Run backtest
│   ├── 03_paper_trade.sh    # Paper trading
│   └── 04_live_trade.sh     # Live trading (use with caution!)
└── notebooks/               # Jupyter notebooks for analysis
    ├── model_evaluation.ipynb
    └── backtest_analysis.ipynb
```

## Quick Start Workflows

### Workflow 1: Simple Moving Average Strategy

**No training required - works immediately!**

```bash
# 1. Backtest with historical data
ibkr-trader backtest data/AAPL_2024.csv \
  --strategy sma \
  --fast 10 \
  --slow 20 \
  --size 10

# 2. Paper trade (requires TWS/Gateway on port 7497)
ibkr-trader run \
  --symbol AAPL \
  --fast 10 \
  --slow 20 \
  --size 10

# 3. Live trade (when ready - requires confirmation)
IBKR_TRADING_MODE=live ibkr-trader run \
  --symbol AAPL \
  --fast 10 \
  --slow 20 \
  --live
```

### Workflow 2: Industry Model (ML-Based)

**Full ML pipeline: train → backtest → trade**

```bash
# 1. Train the model
ibkr-trader train-model \
  --target AAPL \
  --peer MSFT \
  --peer GOOGL \
  --start 2023-01-01 \
  --end 2024-01-01 \
  --horizon 5 \
  --artifact-dir model/artifacts/aapl_model

# 2. Backtest with trained model
ibkr-trader backtest data/AAPL_2024.csv \
  --strategy industry \
  --model-artifact model/artifacts/aapl_model/industry_model.json \
  --entry-threshold 0.01

# 3. Paper trade with model
# See examples/scripts/03_paper_trade.sh for full setup
```

### Workflow 3: Config-Based Advanced Strategies

**Use JSON configs for complex strategies**

```bash
# 1. Create or edit config
cp examples/configs/mean_reversion.json my_strategy.json
# Edit my_strategy.json with your parameters

# 2. Run with config (new in Phase 1!)
ibkr-trader run --config my_strategy.json

# 3. Backtest config-based strategies (replay mode)
# See examples/scripts/backtest_advanced.py
```

### Workflow 4: Market Replay with Order Book Data

**Test market making strategies with historical L2 data**

```bash
# Run replay simulation
python examples/scripts/replay_market_maker.py \
  --order-book data/order_book_AAPL.csv \
  --config examples/configs/fixed_spread_mm.json
```

## Example Strategies

### 1. Simple Moving Average (SMA)

**File:** `strategies/sma_example.py`

Classic trend-following strategy using moving average crossovers.

**Parameters:**
- `fast_period`: Short-term MA window (default: 10)
- `slow_period`: Long-term MA window (default: 20)
- `position_size`: Shares per trade (default: 10)

**Use Case:** Good for trending markets, simple to understand

### 2. Mean Reversion

**File:** `strategies/mean_reversion.py`

Z-score based entry/exit with dynamic stop loss.

**Parameters:**
- `lookback`: Statistical window (default: 20)
- `entry_zscore`: Entry threshold (default: 2.0)
- `exit_zscore`: Exit threshold (default: 0.5)
- `stop_multiple`: Stop loss multiplier (default: 2.0)

**Use Case:** Range-bound markets, stocks with predictable volatility

### 3. Industry Model (ML)

**File:** `strategies/industry_model.py`

Machine learning strategy using peer correlation to forecast returns.

**Training Required:** Yes

**Parameters:**
- `target`: Stock to trade
- `peers`: List of correlated stocks
- `horizon`: Forecast horizon in days
- `entry_threshold`: Minimum edge to trade

**Use Case:** Exploiting sector rotation, pairs trading

### 4. Market Maker

**File:** `strategies/market_maker.py`

Basic market making with fixed spread and inventory management.

**Parameters:**
- `spread`: Bid-ask spread (default: 0.1)
- `quote_size`: Size per level (default: 1)
- `inventory_limit`: Max position (default: 5)

**Use Case:** High-frequency trading, liquidity provision

**Note:** Requires L2 order book data (see `docs/architecture/order_book_implementation.md`)

## Configuration Files

All configs in `examples/configs/` follow the same structure:

```json
{
  "name": "Strategy Name",
  "strategy_type": "mean_reversion",
  "symbol": "AAPL",
  "data": {
    "order_book": [],
    "trades": [],
    "option_surface": []
  },
  "execution": {
    "lookback_short": 20,
    "lookback_long": 60,
    "entry_zscore": 2.0,
    "exit_zscore": 0.5
  },
  "risk": {
    "inventory_limit": 10,
    "max_drawdown": 0.05,
    "kill_switch": true
  }
}
```

**Supported Strategy Types:**
- `fixed_spread_mm` - Market making
- `mean_reversion` - Statistical arbitrage
- `vol_overlay` - Volatility-based sizing
- `skew_arb` - Options skew arbitrage
- `microstructure_ml` - ML microstructure prediction
- `regime_rotation` - Multi-regime portfolio
- `vol_spillover` - Cross-asset volatility trading

## Scripts Reference

### Training Scripts

**`scripts/01_train_model.sh`**
- Trains industry model with peer stocks
- Downloads historical data via IBKR or yfinance
- Saves model artifacts and predictions
- Generates evaluation plots

### Backtesting Scripts

**`scripts/02_backtest.sh`**
- Runs strategy against historical data
- Generates performance metrics
- Plots P&L curves
- Calculates Sharpe ratio, max drawdown

### Paper Trading Scripts

**`scripts/03_paper_trade.sh`**
- Connects to IBKR paper account
- Runs strategy in real-time
- Logs all trades and positions
- Safe testing environment

### Live Trading Scripts

**`scripts/04_live_trade.sh`**
- **⚠️ REAL MONEY AT RISK ⚠️**
- Requires explicit confirmation
- Logs everything for audit
- Use only after thorough testing

## Data Requirements

### For Training

**Format:** CSV with columns:
```
date,open,high,low,close,volume
2024-01-01,150.00,152.00,149.50,151.00,1000000
```

**Sources:**
- IBKR historical data (via CLI)
- yfinance (default)
- Your own data provider

### For Backtesting

**Minimum:** CSV with `timestamp` and `close` columns
**Optimal:** Full OHLCV data

### For Replay

**Order Book:** CSV with L2 depth data
**Trades:** CSV with tick-by-tick trades
**Options:** CSV with option surface snapshots

## Safety Guidelines

### Before Live Trading

✅ **Required Steps:**
1. Backtest strategy for at least 6 months of data
2. Paper trade for at least 2 weeks
3. Review ALL logs and trades manually
4. Set strict risk limits (`max_daily_loss`, `max_order_exposure`)
5. Start with small position sizes
6. Monitor actively during first week

❌ **Never Do:**
1. Skip paper trading phase
2. Trade with more capital than you can afford to lose
3. Use untested strategies in live mode
4. Ignore risk warnings
5. Trade without understanding the strategy logic

### Risk Management

**Default Limits (in `.env`):**
```bash
MAX_DAILY_LOSS=500        # Stop trading after $500 loss
MAX_ORDER_EXPOSURE=5000   # Max $5000 per order
IBKR_PORT=7497           # Paper trading port (7496 = live)
```

**Always:**
- Use stop losses
- Monitor positions regularly
- Have a kill switch ready
- Keep logs for tax reporting

## Customization Guide

### Modifying Strategies

1. **Copy example:** `cp strategies/mean_reversion.py my_strategy.py`
2. **Edit parameters:** Adjust in `__init__()` method
3. **Modify logic:** Change `on_bar()` callback
4. **Test thoroughly:** Run backtest first

### Creating New Strategies

See `docs/guides/strategies/strategy_quick_start.md` for templates:
1. Choose template (price-based, market making, ML, portfolio)
2. Copy template to `examples/strategies/`
3. Implement your logic
4. Create corresponding config in `examples/configs/`
5. Test with SimulatedBroker first

### Adding New Features

1. **New indicators:** Add to strategy's `__init__()` and `on_bar()`
2. **New data sources:** Implement in `model/data/` and use in training
3. **New risk rules:** Extend `RiskGuard` in `ibkr_trader/portfolio.py`

## Troubleshooting

### "Connection refused" Error

**Problem:** Can't connect to IBKR
**Solution:**
1. Start TWS or IB Gateway
2. Check port (7497 for paper, 7496 for live)
3. Enable API in TWS settings
4. Verify `IBKR_PORT` in `.env`

### "Invalid contract" Error

**Problem:** Symbol not recognized
**Solution:**
1. Check symbol spelling
2. Try with exchange: `AAPL@SMART`
3. Qualify contract manually first

### "No market data" Error

**Problem:** Not receiving price updates
**Solution:**
1. Check market hours
2. Verify market data subscriptions in IBKR
3. Try with mock data: `USE_MOCK_MARKET_DATA=true`

### Model Training Fails

**Problem:** Training crashes or produces poor results
**Solution:**
1. Check data quality (no NaNs, sufficient history)
2. Verify peer symbols are correlated
3. Try different horizon (3-10 days typically good)
4. Check for data alignment issues

## Performance Tips

1. **Use caching:** Enable market data cache for training
2. **Parallel backtests:** Run multiple configs simultaneously
3. **Optimize parameters:** Use grid search (see notebooks)
4. **Monitor resources:** Watch CPU/memory during live trading

## Next Steps

1. **Start small:** Run SMA example to learn the system
2. **Train a model:** Follow industry model workflow
3. **Paper trade:** Test in live environment (no risk)
4. **Analyze results:** Use notebooks to evaluate performance
5. **Iterate:** Refine parameters and logic
6. **Scale gradually:** Increase position sizes slowly

## Resources

- **Monitoring Guide:** `docs/guides/operations/monitoring_guide.md` - Real-time dashboard and session analysis
- **Strategy Guide:** `docs/guides/strategies/unified_strategy_guide.md` - Complete strategy development
- **Quick Start:** `docs/guides/strategies/strategy_quick_start.md` - Strategy templates
- **Order Book:** `docs/architecture/order_book_implementation.md` - L2 market depth
- **Model Training:** `docs/guides/data/model_training_guide.md` - ML model workflows
- **Repository Guide:** `CLAUDE.md` - Development reference

## Support

For questions or issues:
1. Check `CLAUDE.md` for development guidance
2. Review existing examples
3. Read strategy documentation
4. Test with SimulatedBroker first

---

**Remember:** Trading involves risk. These examples are for educational purposes. Always test thoroughly before risking real capital.
