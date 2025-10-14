# IBKR Personal Trader

A **safe, modular, and type-safe** trading platform for Interactive Brokers TWS, built with Python 3.12+.

## 🛡️ Safety First

**This platform defaults to PAPER TRADING mode.** Live trading requires explicit flags and multi-step acknowledgment to prevent accidental real-money trades.

### Safety Features

- ✅ Paper trading by default
- ✅ `LiveTradingGuard` prevents accidental live orders
- ✅ Multi-step authentication for live trading
- ✅ Position size limits
- ✅ Daily loss limits
- ✅ Type-safe with `mypy --strict`
- ✅ Pydantic v2 validation on all I/O

## 🏗️ Architecture

Clean, modular design following best practices:

- **Config**: Pydantic v2 settings with environment variable support
- **Safety**: `LiveTradingGuard` with multi-layer protection
- **Broker**: Async IBKR connection manager that also publishes order status events and supports advanced order types
- **Events**: Lightweight asyncio pub/sub bus keeping market data and order updates decoupled
- **Market Data**: Service layer for throttled subscriptions and external price feeds
- **Portfolio**: In-memory portfolio/risk tracking with daily loss and exposure guardrails,
  persisted to `data/portfolio_snapshot.json` by default for resilience
- **Sim/Backtest**: Drop-in simulated broker/market data components and reusable engine for backtests (`ibkr_trader/sim`, `ibkr_trader/backtest`)
- **Model**: Offline training pipelines (`model/training`) and artifact loaders (`model/registry`) kept separate from runtime execution
- **Strategy**: Event-driven strategies that subscribe to bus updates and submit orders through a shared context
- **CLI**: Typer-based command-line interface

## 📦 Installation

### Prerequisites

1. **Interactive Brokers Account** (Paper or Live)
2. **TWS or IB Gateway** running locally
3. **Python 3.12+**
4. **uv** (recommended) or pip

### Setup

```bash
# Clone the repository
git clone <your-repo>
cd ibkr-personal-trader

# Install with uv (recommended)
uv pip install -e ".[dev]"

# Or with pip
pip install -e ".[dev]"
```

### Configure TWS/Gateway

1. Start TWS or IB Gateway
2. Enable API connections:
   - TWS: Configure → API → Settings
   - Check "Enable ActiveX and Socket Clients"
   - Add `127.0.0.1` to trusted IPs
3. Note the port:
   - **Paper Trading**: Port `7497`
   - **Live Trading**: Port `7496`

### Environment Configuration

Create a `.env` file:

```bash
# Paper trading (default - safe)
IBKR_TRADING_MODE=paper
IBKR_PORT=7497
IBKR_HOST=127.0.0.1

# Safety limits
IBKR_MAX_POSITION_SIZE=100
IBKR_MAX_DAILY_LOSS=1000.0
IBKR_MAX_ORDER_EXPOSURE=10000.0
IBKR_USE_MOCK_MARKET_DATA=true  # Set to false to stream real market data (requires entitlements)
```

## 🚀 Quick Start

### Check Connection

```bash
ibkr-trader status

# Optional: preview an order without sending it
ibkr-trader paper-order --symbol AAPL --quantity 1 --preview

# Trade non-US instruments (example: EUR/USD FX via IDEALPRO)
ibkr-trader paper-order --symbol EUR --sec-type CASH --exchange IDEALPRO --currency USD --quantity 10000 --preview

# Ultra-fast preset trades
ibkr-trader paper-quick spy --preview
ibkr-trader paper-quick spy --side SELL

# Submit a 1-share paper trade market order
ibkr-trader paper-order --symbol AAPL --quantity 1

# Run a backtest over a CSV of historical data
ibkr-trader backtest data/AAPL_1d.csv --symbol AAPL --timestamp date --price close
```

### Run Strategy (Paper Trading)

```bash
# Simple moving average strategy
ibkr-trader run --symbol AAPL --symbol MSFT

# Custom SMA periods
ibkr-trader run --symbol AAPL --fast 5 --slow 15

# Adjust position size
ibkr-trader run --symbol AAPL --size 50

# Verbose logging
ibkr-trader run --symbol AAPL -v
```

## 📘 Documentation

- [Quick Start Guide](QUICKSTART.md) — installation and first trades.
- [Model Training & Data Caching Guide](docs/model_training_guide.md) — step-by-step walkthrough for preparing data, caching option chains, and training the sample model.

## ⚠️ Live Trading (Use with Extreme Caution)

Live trading requires **three explicit steps**:

### 1. Set Environment Variable

```bash
export IBKR_TRADING_MODE=live
export IBKR_PORT=7496
```

### 2. Pass `--live` Flag

```bash
ibkr-trader run --symbol AAPL --live
```

### 3. Confirm When Prompted

The system will display a warning and ask for explicit confirmation:

```
⚠️  LIVE TRADING MODE DETECTED  ⚠️
You are about to trade with REAL MONEY
This can result in REAL FINANCIAL LOSS

Do you acknowledge the risks and want to proceed with LIVE trading? [y/N]:
```

**Only after all three steps will live trading be enabled.**

### Market Data Costs & Limits

- IBKR charges real-time data per exchange on the linked live account, even if you connect in paper mode.
- By default the platform only publishes internal mock data; enable real feeds explicitly in configuration if you have paid entitlements.
- Historical data requests via IBKR count against market data permissions but do not add extra fees; consider free sources (e.g., Yahoo Finance) for backtesting or long lookbacks.
- Strategies should stay within the exchange’s streaming limits—keep concurrent subscriptions modest to avoid IBKR throttling.

## 📊 Built-in Strategy: SMA Crossover

A simple moving average crossover strategy for testing:

- **Buy Signal**: Fast SMA crosses above Slow SMA
- **Sell Signal**: Fast SMA crosses below Slow SMA

**Parameters**:
- `--fast`: Fast SMA period (default: 10)
- `--slow`: Slow SMA period (default: 20)
- `--size`: Position size per trade (default: 10)

## 🧪 Development

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=ibkr_trader --cov-report=html

# Run specific test file
pytest tests/test_safety.py
```

### Type Checking

```bash
mypy ibkr_trader
```

### Linting & Hooks

```bash
# Run formatting, linting, and syntax checks
./linter.sh

# (once per clone) enable project git hooks
git config core.hooksPath .githooks
```

## 🔧 Creating Custom Strategies

Extend the `Strategy` base class:

```python
from decimal import Decimal
from ibkr_trader.strategy import Strategy, StrategyConfig
from ibkr_trader.models import OrderSide

class MyStrategy(Strategy):
    async def on_bar(self, symbol: str, price: Decimal) -> None:
        """Process new price data."""
        # Your strategy logic here
        current_position = await self.get_position(symbol)
        
        if self.should_buy(symbol, price):
            await self.place_market_order(
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=self.config.position_size
            )
```

## 📁 Project Structure

```
ibkr-personal-trader/
├── ibkr_trader/
│   ├── __init__.py
│   ├── config.py          # Configuration management
│   ├── safety.py          # LiveTradingGuard
│   ├── models.py          # Pydantic v2 models
│   ├── broker.py          # IBKR connection
│   ├── strategy.py        # Strategy base & implementations
│   └── cli.py             # Typer CLI
├── tests/
│   ├── test_safety.py     # Safety guard tests
│   └── test_models.py     # Model validation tests
├── pyproject.toml
├── .env                   # Your local config (gitignored)
└── README.md
```

## 🔒 Safety Philosophy

1. **Paper by Default**: All trading defaults to paper mode
2. **Explicit Intent**: Live trading requires multiple explicit actions
3. **Defense in Depth**: Multiple layers of safety checks
4. **Type Safety**: Strict type checking with mypy
5. **Validation**: Pydantic v2 validates all inputs
6. **Logging**: Comprehensive logging of all actions

## ⚡ Features

- ✅ Paper trading by default
- ✅ Type-safe with `mypy --strict`
- ✅ Pydantic v2 for all I/O
- ✅ Async/await for concurrent operations
- ✅ Comprehensive logging with loguru
- ✅ CLI with typer
- ✅ Full test coverage
- ✅ Modular, clean architecture
- ✅ Position size limits
- ✅ Daily loss limits
- ✅ Real-time market data support
- ✅ Order execution with safety guards

## 🎯 Roadmap

- [ ] Backtesting framework
- [ ] More built-in strategies
- [ ] Risk management module
- [ ] Performance analytics
- [ ] Database persistence
- [ ] WebSocket market data
- [ ] Multiple broker support

## ⚠️ Disclaimer

**This software is for educational purposes only. Trading involves substantial risk of loss. The authors are not responsible for any financial losses incurred through use of this software.**

Always:
- Start with paper trading
- Test thoroughly before going live
- Never risk more than you can afford to lose
- Understand your strategy completely
- Monitor your positions actively

## 📝 License

MIT License - see LICENSE file for details

## 🤝 Contributing

Contributions welcome! Please:

1. Maintain type safety (`mypy --strict`)
2. Add tests for new features
3. Follow existing code style
4. Update documentation
5. Never compromise safety features

## 📞 Support

- Issues: [GitHub Issues](https://github.com/your-repo/issues)
- Docs: [Full Documentation](https://docs.your-site.com)

---

**Remember: Paper trade first, then consider live trading only after thorough testing. Your capital is at risk.**
