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
4. Add `127.0.0.1` to trusted IPs
5. Note the port (default paper: `7497`)

## 4. Create Environment File

```bash
cp .env.example .env
```

Edit `.env`:
```bash
IBKR_TRADING_MODE=paper
IBKR_PORT=7497
IBKR_HOST=127.0.0.1
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
📄  IBKR PERSONAL TRADER  📄
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

## Common Issues

### "Connection refused"
- **Solution**: Ensure TWS/Gateway is running
- Check port matches your configuration
- Verify API access is enabled

### "Invalid client ID"
- **Solution**: Use a unique client ID
- Set `IBKR_CLIENT_ID=2` in `.env` if ID 1 is in use

### "No market data"
- **Solution**: Subscribe to market data in TWS
- Paper trading accounts have delayed data by default

## Next Steps

1. **Monitor Logs**: Check `logs/` directory for detailed activity
2. **Review Positions**: Use `ibkr-trader status` anytime
3. **Test Thoroughly**: Run paper trading for several days
4. **Read Docs**: See `README.md` for advanced features

## Development Workflow

```bash
# Run tests
pytest

# Type check
mypy ibkr_trader

# Lint and format
ruff check ibkr_trader
ruff format ibkr_trader
```

## Safety Reminder

**Never skip paper trading!** Always:

1. ✅ Test new strategies in paper mode
2. ✅ Run for at least a week in paper mode
3. ✅ Verify all logs and behavior
4. ✅ Understand every order placed
5. ✅ Start with small position sizes in live mode

---

**Happy (paper) trading! 📄💹**