# Quick Start Guide

Get up and running with IBKR Personal Trader in 5 minutes.

## 1. Prerequisites

- Python 3.12+
- Interactive Brokers paper trading account
- TWS or IB Gateway installed

## 2. Install

```bash
# Clone and install
git clone <your-repo>
cd ibkr-personal-trader
uv pip install -e ".[dev]"
```

## 3. Configure TWS/Gateway

### Start TWS or IB Gateway
- Launch the application
- Log in with your paper trading credentials

### Enable API Access
1. **TWS**: Edit → Global Configuration → API → Settings
2. **Gateway**: Configure → Settings → API
3. Enable "Enable ActiveX and Socket Clients"
4. Add `127.0.0.1` to trusted IPs (or WSL IP if running from WSL - see below)
5. Note the port (default paper: `7497`)

### WSL Users (Windows)
If running from WSL, find the Windows host IP with PowerShell:
```powershell
Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object {$_.InterfaceAlias -like "vEthernet*"} |
  Select-Object InterfaceAlias,IPAddress,PrefixLength
```

## 4. Create Environment File

```bash
cp .env.example .env
```

Edit `.env`:
```bash
IBKR_TRADING_MODE=paper
IBKR_PORT=7497
IBKR_HOST=127.0.0.1  # Or 192.168.x.x for WSL (see above)
```

## 5. Test Connection

```bash
ibkr-trader status
```

Expected output:
```
ACCOUNT STATUS
======================================================================
Account Type: PAPER
Net Liquidation: $100,000.00
...
```

## 6. Run Your First Strategy

```bash
# Paper trade AAPL with default settings
ibkr-trader run --symbol AAPL
```

Expected output:
```
======================================================================
IBKR PERSONAL TRADER
Mode: PAPER
Port: 7497
Symbols: AAPL
======================================================================
Strategy initialized: SMA_Crossover
Fast SMA: 10, Slow SMA: 20
Starting price monitoring... (Press Ctrl+C to stop)
```

## 7. Customize Strategy

```bash
# Multiple symbols
ibkr-trader run --symbol AAPL --symbol MSFT --symbol GOOGL

# Custom SMA periods
ibkr-trader run --symbol AAPL --fast 5 --slow 15

# Larger position size
ibkr-trader run --symbol AAPL --size 50

# Verbose logging
ibkr-trader run --symbol AAPL -v
```

## 8. Run Multiple Strategies

```bash
ibkr-trader run --config strategy_configs/examples/dual_sma.graph.json
```

Any `--config` path ending with `.graph.json` spins up the strategy coordinator so you
can run multiple strategies at once. The included demo graph launches two SMA
configurations (AAPL + MSFT) with independent sizing caps while sharing the same broker
and market data streams.

## 9. Configure Per-Symbol Limits

```bash
# Apply tighter limits to TSLA
ibkr-trader set-symbol-limit --symbol TSLA \
  --max-position 15 --max-exposure 3500 --max-loss 200

# Update fallback defaults for every other symbol
ibkr-trader set-symbol-limit --default --max-position 50 --max-loss 500
```

This command updates `data/symbol_limits.json` and prevents orders that exceed the
per-symbol position, exposure, or daily loss thresholds. The live dashboard will show
limit utilisation so you can spot symbols that are nearing their cap.

## Common Issues

### "Connection refused" or "Connection timeout"
- **Solution**: Ensure TWS/Gateway is running
- Check port matches your configuration (7497 for paper, 7496 for live)
- Verify API access is enabled
- **WSL Users**: Use Windows host IP (192.168.x.x) instead of 127.0.0.1

### "Invalid client ID"
- **Solution**: Use a unique client ID
- Set `CLIENT_ID=2` in `.env` if ID 1 is in use
- Each connection needs a unique client ID

### "No market data"
- **Solution**: Subscribe to market data in TWS
- Paper trading accounts have delayed data by default

## Next Steps

1. **Monitor Logs**: Check `logs/` directory for detailed activity
2. **Review Positions**: Use `ibkr-trader status` anytime
3. **Test Thoroughly**: Run paper trading for several days
4. **Tune Risk Limits**: Run `ibkr-trader set-symbol-limit` and maintain `data/correlation_matrix.json`
5. **Read Docs**: See `README.md` for advanced features

### Offline Model Training

```bash
# Train the sample regression model with cached market data
uv run python trained_models/train_model_example.py

# Or use the CLI so you can swap data sources and cache dirs
ibkr-trader train-model --target AAPL --peer MSFT --peer GOOGL --start 2023-01-01 --end 2024-01-01
```

### Cache Option Chains

```bash
# Store an AAPL option chain (yfinance by default)
ibkr-trader cache-option-chain --symbol AAPL --expiry 2024-01-19

# Use IBKR snapshots with custom limits
ibkr-trader cache-option-chain --symbol AAPL \
  --expiry 2024-01-19 \
  --data-source ibkr \
  --max-snapshots 20 \
  --snapshot-interval 2.0 \
  --ibkr-client-id 210
```

## Development Workflow

```bash
# Run tests (quiet mode by default)
uv run pytest

# Type check (token-efficient)
uv run mypy ibkr_trader --no-error-summary

# Lint and format
uv run ruff check ibkr_trader
uv run ruff format ibkr_trader
```

## Safety Reminder

**Never skip paper trading!** Always:

1. Test new strategies in paper mode
2. Run for at least a week in paper mode
3. Verify all logs and behavior
4. Understand every order placed
5. Start with small position sizes in live mode

---

Happy paper trading!
