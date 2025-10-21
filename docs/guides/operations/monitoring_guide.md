# Monitoring & Observability Guide

Complete guide to monitoring your trading sessions with IBKR Personal Trader.

---

## Table of Contents

1. [Overview](#overview)
2. [Real-Time Dashboard](#real-time-dashboard)
3. [Session Summaries](#session-summaries)
4. [Graceful Shutdown](#graceful-shutdown)
5. [Telemetry & Diagnostics](#telemetry--diagnostics)
6. [Best Practices](#best-practices)
7. [Troubleshooting](#troubleshooting)

---

## Overview

The monitoring system provides three layers of observability:

1. **Real-Time Monitoring**: Live dashboard during trading
2. **Session Summaries**: Post-session performance analysis
3. **Historical Telemetry**: Audit logs for debugging

### Key Features

- Live P&L tracking with risk indicators
- Position monitoring with size warnings
- Graceful shutdown with open position alerts
- Trade performance metrics (win rate, avg P&L)
- Comprehensive telemetry logging

---

## Real-Time Dashboard

### Command

```bash
ibkr-trader dashboard [--verbose]
```

### Description

Launches a full-screen terminal dashboard that updates in real-time (2Hz refresh rate). Connects to IBKR and displays live trading activity.

### Prerequisites

1. **IBKR Connection**: TWS or IB Gateway must be running
2. **Port Configuration**: Port 7497 (paper) or 7496 (live) in `.env`
3. **Active Positions**: Dashboard shows best with existing positions

### Dashboard Layout

```
┌──────────────────────────────────────────────────────────┐
│           IBKR Trading Dashboard                         │
│           2025-10-17 14:30:45 UTC                       │
└──────────────────────────────────────────────────────────┘
┌─────────────────────┬────────────────────────────────────┐
│  Account Summary    │  Positions                         │
│                     │                                    │
│  Net Liquidation:   │  Symbol  Qty  AvgPrice  P&L       │
│  $50,000.00         │  AAPL    10   $150.00  +$50.00   │
│                     │  MSFT    -5   $300.00  -$25.00   │
│  Realized P&L:      │                                    │
│  +$125.50           ├────────────────────────────────────┤
│                     │  Recent Activity                   │
│  Risk Status:       │                                    │
│  OK (15%)           │  14:30:42  FILL   AAPL  BUY 10    │
│                     │  14:30:40  ORDER  AAPL  SUBMITTED │
└─────────────────────┴────────────────────────────────────┘
│  Ctrl+C: Exit  |  Auto-refresh: 2Hz                     │
└──────────────────────────────────────────────────────────┘
```

### Panel Details

#### 1. Account Summary (Left Panel)

**Displays:**
- **Net Liquidation**: Total account value
- **Cash**: Available cash balance
- **Buying Power**: Margin buying power
- **Realized P&L (Today)**: Profit/loss from closed positions
- **Unrealized P&L**: Profit/loss from open positions
- **Risk Status**: Daily loss limit indicator

**Risk Status Colors:**
- 🟢 **OK** (< 80%): Normal operation
- 🟡 **WARNING** (80-90%): Approaching daily loss limit
- 🔴 **CRITICAL** (≥ 90%): Near or at daily loss limit

#### 2. Positions Panel (Top Right)

**Displays:**
- **Symbol**: Ticker symbol
- **Qty**: Position size (positive = long, negative = short)
- **Avg Price**: Average entry price
- **Market Price**: Current market price (live)
- **P&L**: Unrealized profit/loss (color-coded)
- **Limit Util**: Current vs allowed size (⚠ when > 80%; `*` denotes symbol-specific limit)

**Color Coding:**
- 🟢 Green: Profitable position
- 🔴 Red: Losing position
- 🟡 Yellow ⚠: Position size 80-90% of limit
- 🔴 Red ⚠: Position size ≥ 90% of limit (orders blocked at 100%)

#### 3. Activity Feed (Bottom Right)

**Shows last 15 events:**
- **ORDER**: Order status changes (SUBMITTED, FILLED, CANCELLED)
- **FILL**: Execution confirmations with price

**Format:**
```
TIME      TYPE   SYMBOL  DETAILS
14:30:42  FILL   AAPL    BUY 10@$150.50
14:30:40  ORDER  AAPL    SUBMITTED BUY 5/10
```

### Usage Examples

#### Monitor Active Trading Session

```bash
# Launch dashboard while strategy is running (separate terminal)
ibkr-trader dashboard
```

#### Check Portfolio Without Trading

```bash
# View current positions and P&L
ibkr-trader dashboard
# Press Ctrl+C to exit
```

#### Verbose Mode (Debug)

```bash
# Show detailed connection logs
ibkr-trader dashboard --verbose
```

### Keyboard Controls

- **Ctrl+C**: Exit dashboard gracefully
- Dashboard auto-refreshes, no manual input needed

### Limitations

- **Update Frequency**: 2Hz (500ms intervals)
- **Market Data**: Requires IBKR market data subscriptions for live prices
- **Position Limit**: Optimized for < 20 positions (more will scroll)
- **Connection**: Dashboard exits if IBKR connection drops

---

## Session Summaries

### Overview

Session summaries provide post-execution analysis of trading performance. Automatically generated after:
- Strategy execution completes
- Backtest finishes
- Dashboard exits
- Manual `session-status` command

### Command

```bash
ibkr-trader session-status [--tail N]
```

**Options:**
- `--tail N`: Show last N telemetry entries (default: 5)

### Summary Components

#### 1. Headline Metrics

```
NetLiq=$50,000.00 | Cash=$25,000.00 | Positions=2 | Trades=15 | WinRate=66.7%
```

**Fields:**
- **NetLiq**: Net liquidation value
- **Cash**: Available cash
- **Positions**: Open position count
- **Trades**: Total fills executed
- **WinRate**: % of symbols with positive P&L

#### 2. Trade Statistics

```json
{
  "fills": "15",
  "buy_volume": "75000.00",
  "sell_volume": "70000.00",
  "realized_pnl": "1250.50",
  "symbol_pnl": {
    "AAPL": "850.25",
    "MSFT": "400.25"
  }
}
```

**Metrics Explained:**
- **fills**: Total number of executions
- **buy_volume**: Total $ bought
- **sell_volume**: Total $ sold
- **realized_pnl**: Net P&L from closed trades
- **symbol_pnl**: Per-symbol breakdown

#### 3. Recommended Actions

Automatically generated suggestions based on session data:

```
Recommended actions (paper-run):
  - Review high number of open positions.
  - Review symbol performance: best=AAPL ($850.25) worst=TSLA (-$125.00)
  - Cash below 10% of NetLiq; consider raising cash.
```

**Action Types:**
- High position count warnings
- Low cash ratio alerts
- Symbol performance highlights
- Cache/rate limit warnings

#### 4. Telemetry Warnings

Recent errors/warnings from session:

```
Recent telemetry warnings (paper-run):
  2025-10-17T14:30:45Z WARNING: Position size approaching limit for AAPL
  2025-10-17T14:25:12Z ERROR: Order rejected: insufficient buying power
```

### Example Session

```bash
$ ibkr-trader session-status

=== Session Status ===
Snapshot file: data/portfolio_snapshot.json
NetLiq=$50,000.00 | Cash=$25,000.00 | Positions=2 | Trades=15 | WinRate=66.7%

Positions:
  AAPL: 10
  MSFT: -5

Telemetry file: logs/telemetry.jsonl
Recent telemetry warnings:
  2025-10-17T14:30:45Z WARNING: Approaching daily loss limit (85%)
```

---

## Graceful Shutdown

### Overview

When you interrupt a running strategy with **Ctrl+C**, the platform performs a graceful shutdown with a comprehensive final summary.

### Shutdown Sequence

1. **Interrupt Detected**
   ```
   ======================================================================
   SHUTDOWN INITIATED BY USER
   ======================================================================
   ```

2. **Strategy Cleanup**
   - Stop strategy logic
   - Cancel pending orders (optional)
   - Flush telemetry logs

3. **Final Summary Display**
   ```
   ======================================================================
   FINAL SESSION SUMMARY
   ======================================================================
   Realized P&L: $1,250.50
   Total Fills: 15

   Per-Symbol P&L:
     AAPL: $850.25
     MSFT: $400.25

   ⚠ OPEN POSITIONS DETECTED ⚠

   You have 2 open position(s):
     AAPL: +10 shares @ $150.00 | Unrealized P&L: +$50.00
     MSFT: -5 shares @ $300.00 | Unrealized P&L: -$25.00

   Remember to close these positions if needed!
   ======================================================================
   ```

4. **Broker Disconnection**
   - Close IBKR connection
   - Persist portfolio snapshot

### Open Position Warnings

**Critical Safety Feature:** If you have open positions when shutting down, you'll see:

```
⚠ OPEN POSITIONS DETECTED ⚠

You have 2 open position(s):
  AAPL: +10 shares @ $150.00 | Unrealized P&L: +$50.00
  MSFT: -5 shares @ $300.00 | Unrealized P&L: -$25.00

Remember to close these positions if needed!
```

**Why This Matters:**
- Prevents forgotten overnight positions
- Shows current P&L for decision-making
- Reminds you to manage risk

**No Open Positions:**
```
✓ All positions closed
```

### Best Practices

#### DO:
- ✅ Review the final summary before closing terminal
- ✅ Check open positions and decide whether to close them
- ✅ Note per-symbol P&L for tax records
- ✅ Review warnings for any issues

#### DON'T:
- ❌ Force-kill the process (Ctrl+Z, `kill -9`)
- ❌ Ignore open position warnings
- ❌ Close terminal before reading summary
- ❌ Disconnect TWS before shutdown completes

---

## Telemetry & Diagnostics

### Telemetry Logging

All platform events are logged to `logs/telemetry.jsonl` in JSON Lines format.

**File Location:**
```
logs/telemetry.jsonl
```

**Format:**
```json
{"level":"INFO","message":"Strategy started","timestamp":"2025-10-17T14:30:00Z","context":{"symbols":["AAPL"]}}
{"level":"WARNING","message":"Position size approaching limit","timestamp":"2025-10-17T14:30:45Z","context":{"symbol":"AAPL","size":9,"limit":10}}
```

### Monitoring Telemetry

#### Live Monitoring

```bash
# Watch telemetry in real-time
ibkr-trader monitor-telemetry --follow

# Show last 50 entries
ibkr-trader monitor-telemetry --tail 50
```

#### Grep for Errors

```bash
# Find all errors
grep -i error logs/telemetry.jsonl

# Find warnings for specific symbol
grep -i "aapl.*warning" logs/telemetry.jsonl
```

### Diagnostics Command

Check cache status and rate limits:

```bash
ibkr-trader diagnostics [--show-metadata]
```

**Output:**
```
=== Market Data Diagnostics ===
Price cache directory: data/training_cache (ttl=1 h)
Option chain cache directory: data/training_cache/option_chains (ttl=1 h)
IBKR rate limit usage: 15/50 requests this session
```

**With Metadata:**
```bash
ibkr-trader diagnostics --show-metadata

Option chain cache entries:
  AAPL 2025-12-19 | age=2 h 15 m | schema=2
  MSFT 2025-12-19 | age=45 m | schema=2
```

---

## Best Practices

### For Live Trading

1. **Always Use Dashboard**
   - Run `ibkr-trader dashboard` in separate terminal
   - Monitor risk indicators continuously
   - Watch for position size warnings

2. **Set Strict Limits**
   ```bash
   # In .env
   MAX_DAILY_LOSS=500
   MAX_ORDER_EXPOSURE=5000
   MAX_POSITION_SIZE=10
   ```

3. **Check Summaries Regularly**
   ```bash
   # Between trading sessions
   ibkr-trader session-status
   ```

4. **Review Telemetry for Errors**
   ```bash
   # After each session
   ibkr-trader monitor-telemetry --tail 100 | grep -i error
   ```

### For Paper Trading

1. **Test Dashboard First**
   - Familiarize yourself with layout
   - Understand risk indicators
   - Practice reading activity feed

2. **Simulate Realistic Scenarios**
   - Test with multiple positions
   - Watch approaching limits
   - Practice graceful shutdown

3. **Analyze Performance**
   - Review win rates
   - Check per-symbol P&L
   - Identify losing strategies

### For Backtesting

1. **Always Check Final Summary**
   ```bash
   ibkr-trader backtest data.csv --strategy sma
   # Review summary at end
   ```

2. **Compare Multiple Runs**
   - Save telemetry for each run
   - Compare trade statistics
   - Optimize parameters based on metrics

---

## Troubleshooting

### Dashboard Issues

#### Dashboard Won't Start

**Error:** `Failed to connect: Connection refused`

**Solutions:**
1. Start TWS or IB Gateway
2. Check port in `.env` (7497 for paper, 7496 for live)
3. Enable API in TWS settings
4. Verify IBKR account is active

#### Dashboard Shows No Positions

**Possible Causes:**
- No positions currently held
- Portfolio snapshot not loaded
- IBKR connection issue

**Solutions:**
```bash
# Check status first
ibkr-trader status

# View snapshot
cat data/portfolio_snapshot.json

# Restart dashboard
ibkr-trader dashboard --verbose
```

#### Dashboard Freezes

**Causes:**
- IBKR connection dropped
- Market data subscription issue
- Too many positions (> 20)

**Solutions:**
- Ctrl+C to exit, reconnect TWS
- Check market data subscriptions in IBKR
- Use `session-status` for large portfolios

### Summary Issues

#### Empty Summary

**Error:** `Portfolio snapshot not available.`

**Cause:** No trading session has run yet or snapshot was deleted.

**Solutions:**
```bash
# Run status to create snapshot
ibkr-trader status

# Or run a strategy
ibkr-trader run --symbol AAPL
```

#### Incorrect Metrics

**Issue:** Win rate or P&L doesn't match expectations

**Causes:**
- Snapshot from old session
- Commission not included in realized P&L
- Unrealized P&L excluded from totals

**Solutions:**
- Check snapshot timestamp
- Review `symbol_pnl` in snapshot file
- Remember: Summary shows realized P&L only

### Telemetry Issues

#### Telemetry File Not Found

**Error:** `No telemetry file found at logs/telemetry.jsonl`

**Cause:** No commands have been run yet.

**Solutions:**
```bash
# Run any command to generate telemetry
ibkr-trader status

# Check logs directory
ls -la logs/
```

#### Too Much Telemetry

**Issue:** Telemetry file growing too large

**Solutions:**
```bash
# Archive old logs
mv logs/telemetry.jsonl logs/telemetry_$(date +%Y%m%d).jsonl

# Or clear (careful!)
> logs/telemetry.jsonl
```

---

## Performance Metrics Reference

### Win Rate

**Definition:** Percentage of symbols with positive realized P&L

**Calculation:**
```
win_rate = winning_symbols / total_symbols_traded
```

**Interpretation:**
- **< 40%**: Strategy needs improvement
- **40-60%**: Acceptable for high win-rate strategies
- **> 60%**: Good performance
- **> 80%**: Excellent (verify not overfit)

### Average P&L Per Trade

**Definition:** Mean profit/loss per fill

**Calculation:**
```
avg_pnl = realized_pnl / total_fills
```

**Interpretation:**
- **Positive**: Strategy is profitable overall
- **Negative**: Strategy losing money per trade
- **Large variance**: Risk management needed

### Sharpe Ratio (Future)

Not yet implemented. Planned for Phase 2.

---

## Configuration

### Environment Variables

```bash
# .env file
MAX_DAILY_LOSS=500              # Daily loss limit ($)
MAX_ORDER_EXPOSURE=5000         # Max $ per order
MAX_POSITION_SIZE=10            # Max shares per symbol
IBKR_PORT=7497                  # Paper=7497, Live=7496
IBKR_TRADING_MODE=paper         # paper or live
```

### Dashboard Refresh Rate

Dashboard updates at **2Hz (500ms intervals)**. Not configurable to prevent API overload.

### Telemetry Retention

Logs rotate daily, retained for 7 days by default.

**Configure in code:**
```python
# ibkr_trader/cli.py:93-97
logger.add(
    log_dir / "trader_{time}.log",
    rotation="1 day",
    retention="7 days",  # Change this
    level="DEBUG",
)
```

---

## Examples

### Complete Live Trading Session

```bash
# Terminal 1: Start dashboard
ibkr-trader dashboard

# Terminal 2: Run strategy
ibkr-trader run --symbol AAPL --fast 10 --slow 20

# Monitor in Terminal 1, Ctrl+C in Terminal 2 when done
# Review final summary, check open positions
```

### Paper Trading with Analysis

```bash
# Run paper trading
ibkr-trader run --symbol AAPL

# After session, check summary
ibkr-trader session-status

# Review telemetry
ibkr-trader monitor-telemetry --tail 50

# Check diagnostics
ibkr-trader diagnostics
```

### Backtest Analysis

```bash
# Run backtest
ibkr-trader backtest data/AAPL_2024.csv --strategy sma

# Summary appears automatically
# Review trade statistics and win rate
```

---

## See Also

- [Quick Start Guide](../../../QUICKSTART.md) - Platform setup
- [Strategy Guide](../strategies/unified_strategy_guide.md) - Writing strategies
- [CLI Reference](../../../CLAUDE.md) - All commands
- [Safety Guide](../../../CLAUDE.md#safety-philosophy) - Risk controls
