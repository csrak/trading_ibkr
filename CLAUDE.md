# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

IBKR Personal Trader is a **safety-first** trading platform for Interactive Brokers TWS, built with Python 3.12+. The platform defaults to paper trading and requires explicit multi-step acknowledgment for live trading.

### Safety Philosophy

**CRITICAL**: This is a real-money trading system. The safety mechanisms are the platform's most important features:

1. **Paper trading by default** - `TradingMode.PAPER` is the default in config.py:17
2. **Multi-layer safety guards** - `LiveTradingGuard` in safety.py validates all orders
3. **Three-step live trading** - Requires environment variable, CLI flag, AND user confirmation
4. **Type safety** - `mypy --strict` enforcement (pyproject.toml:59-65)
5. **Pydantic v2 validation** - All I/O validated at runtime

**When modifying code**: Never bypass or weaken safety checks. Always preserve the multi-step live trading guard and position/loss limit validations.

## Development Commands

### Testing

**Default: Quiet Mode** (token-efficient, use for routine checks):
```bash
# Run all tests (quiet output with short tracebacks)
uv run pytest

# Skip slow tests
uv run pytest -m "not slow"

# Run specific test file
uv run pytest tests/test_safety.py

# Run single test
uv run pytest tests/test_safety.py::test_specific_function
```

**Verbose Mode** (use when debugging failures):
```bash
# Full output when you need details
uv run pytest -v

# Very verbose with full tracebacks
uv run pytest -vv

# Stop on first failure
uv run pytest -x

# With coverage report (generates more output)
uv run pytest --cov=ibkr_trader --cov-report=html
```

**Note**: Default pytest behavior is now `-q --tb=short` (configured in `pyproject.toml`) to reduce token usage. Use `-v` explicitly when you need detailed output for debugging.

### Type Checking & Linting

**Token-efficient defaults**:
```bash
# Type check (suppress stats summary)
uv run mypy ibkr_trader --no-error-summary

# Format, lint, and verify syntax (already terse)
./linter.sh

# Manual ruff commands (already token-efficient)
uv run ruff format ibkr_trader tests
uv run ruff check --fix ibkr_trader tests
```

**When debugging type errors**:
```bash
# Full mypy output with error context
uv run mypy ibkr_trader
```

### Git Hooks
```bash
# Enable project hooks (once per clone)
git config core.hooksPath .githooks
```

## Operational Guidelines

- When running long-lived CLI commands (e.g., `ibkr-trader run …`) from automation or shared terminals, wrap the invocation with a timeout (`timeout`, `uv --timeout`, etc.) so sessions never continue unattended.
- Always call out active runs in status updates and confirm they have been stopped (or hand off responsibility) before ending a coding session.

## Architecture Overview

The codebase follows a clean, event-driven architecture with clear separation of concerns:

### Core Layers

1. **Configuration (`config.py`)** - Pydantic v2 settings with `.env` support. All settings prefixed with `IBKR_`.

2. **Safety Layer (`safety.py`)** - `LiveTradingGuard` provides multi-step validation before any order execution. This is NON-NEGOTIABLE.

3. **Broker Interface (`broker.py`)** - `IBKRBroker` manages TWS/Gateway connections via `ib_insync`. Publishes order status and execution events to the event bus. Supports MARKET, LIMIT, STOP, and STOP_LIMIT orders.

4. **Event Bus (`events.py`)** - Lightweight asyncio pub/sub keeping components decoupled:
   - `EventTopic.ORDER_STATUS` - Order lifecycle updates
   - `EventTopic.EXECUTION` - Fill confirmations
   - `EventTopic.MARKET_DATA` - Price updates
   - `EventTopic.DIAGNOSTIC` - Telemetry warnings/info
   - `EventTopic.ACCOUNT` - Account updates

5. **Market Data (`market_data.py`)** - `MarketDataService` manages subscriptions and publishes to the event bus. Supports both real IBKR streams and mock data generation.

6. **Portfolio & Risk (`portfolio.py`)** - `PortfolioState` tracks positions and P&L. `RiskGuard` enforces daily loss limits and order exposure limits. Persists snapshots to `data/portfolio_snapshot.json`.

7. **Strategy (`strategy.py`)** - Event-driven base class. Strategies subscribe to `MARKET_DATA` events and execute trades through broker interface:
   - `SimpleMovingAverageStrategy` - Built-in SMA crossover strategy
   - `IndustryModelStrategy` - ML-based strategy using pre-trained artifacts

8. **CLI (`cli.py`)** - Typer-based commands:
   - `ibkr-trader run` - Execute live strategy (with graceful shutdown)
   - `ibkr-trader dashboard` - Real-time P&L and position monitoring
   - `ibkr-trader status` - Connection and account check
   - `ibkr-trader paper-order` - Single paper order (with `--preview` support)
   - `ibkr-trader paper-quick` - Preset-based quick trades
   - `ibkr-trader backtest` - Backtest on CSV data
   - `ibkr-trader train-model` - Train ML models
   - `ibkr-trader cache-option-chain` - Cache option data
   - `ibkr-trader diagnostics` - Cache and rate limit info
   - `ibkr-trader session-status` - Portfolio snapshot with trade metrics
   - `ibkr-trader monitor-telemetry` - Watch telemetry stream

### Supporting Modules

9. **Simulation (`ibkr_trader/sim/`)** - Drop-in simulated broker and market data for backtesting without IBKR connection.

10. **Backtest Engine (`ibkr_trader/backtest/engine.py`)** - Replay historical bars through strategy logic with simulated execution.

11. **Model Training (`model/training/`)** - Offline training pipelines kept separate from runtime. Uses `MarketDataClient` abstraction over yfinance or IBKR historical data.

12. **Model Inference (`model/inference/`)** - Artifact loaders for trained models used by `IndustryModelStrategy`.

13. **Data Layer (`model/data/`)** - Abstraction over market data sources:
    - `MarketDataClient` - Price bar fetching with caching
    - `OptionChainClient` - Option chain snapshots with caching
    - Sources: `YFinanceMarketDataSource`, `IBKRMarketDataSource`, `YFinanceOptionChainSource`, `IBKROptionChainSource`
    - Cache stores with TTL support: `FileCacheStore`, `OptionChainCacheStore`

14. **Telemetry (`telemetry.py`)** - `TelemetryReporter` logs structured events to `logs/telemetry.jsonl` for post-session analysis.

### Data Flow

```
User Command (CLI)
    ↓
Configuration + Safety Guards
    ↓
Broker Connection (IBKRBroker)
    ↓
Market Data Service ──→ EventBus (MARKET_DATA topic)
    ↓                        ↓
Strategy subscribes ←────────┘
    ↓
Strategy analyzes bar → generates signal
    ↓
Strategy.place_market_order()
    ↓
RiskGuard.validate_order() (daily loss + exposure checks)
    ↓
LiveTradingGuard.check_order_safety() (position size limits)
    ↓
IBKRBroker.place_order() → TWS/Gateway
    ↓
Order status events → EventBus (ORDER_STATUS topic)
    ↓
Execution events → EventBus (EXECUTION topic)
    ↓
Portfolio updates + persistence
```

## Important Implementation Details

### AsyncIO Usage

- All broker operations are `async`
- Event bus uses `asyncio.Queue` for pub/sub
- Strategies run in async event loops
- Use `asyncio.create_task()` for background tasks
- Always handle `CancelledError` on shutdown

### Contract Specifications

`SymbolContract` in models.py:
- `symbol`: Ticker (e.g., "AAPL")
- `sec_type`: "STK", "FUT", "CASH", "OPT" (default: "STK")
- `exchange`: Routing destination (default: "SMART")
- `currency`: Contract currency (default: "USD")

For non-US instruments like forex, specify all fields:
```python
SymbolContract(symbol="EUR", sec_type="CASH", exchange="IDEALPRO", currency="USD")
```

### Order Execution Flow

1. Create `OrderRequest` with contract, side, quantity, order type
2. Broker qualifies contract via `ib.qualifyContractsAsync()`
3. Safety guards validate (LiveTradingGuard + RiskGuard)
4. Order placed via `ib.placeOrder()`
5. Callbacks registered for fills and commission reports
6. Events published to bus for portfolio tracking

### Portfolio Persistence

`PortfolioState` snapshots are persisted to `data/portfolio_snapshot.json` after:
- Account updates
- Position updates
- Order fills
- Execution events

The snapshot includes positions, account metrics, trade stats, and per-symbol P&L.

### Market Data Caching

Training workflows use cached data to minimize IBKR API calls:
- Price bars: `data/cache/` (TTL configurable via `IBKR_TRAINING_PRICE_CACHE_TTL`)
- Option chains: `data/cache/option_chains/` (TTL via `IBKR_TRAINING_OPTION_CACHE_TTL`)
- Rate limiting: `IBKR_TRAINING_MAX_SNAPSHOTS` and `IBKR_TRAINING_SNAPSHOT_INTERVAL`

### Creating Custom Strategies

Extend `Strategy` base class and implement `on_bar()`:

```python
from ibkr_trader.strategy import Strategy, StrategyConfig
from ibkr_trader.models import OrderSide

class MyStrategy(Strategy):
    async def on_bar(self, symbol: str, price: Decimal) -> None:
        current_position = await self.get_position(symbol)

        if self.should_buy(symbol, price):
            await self.place_market_order(
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=self.config.position_size
            )
```

Strategies automatically subscribe to market data events and receive `on_bar()` calls.

### Model Training Workflow

1. Configure data source in `.env`: `IBKR_TRAINING_DATA_SOURCE=yfinance` or `ibkr`
2. Run `ibkr-trader train-model --target AAPL --peer MSFT --peer GOOGL --start 2023-01-01 --end 2024-01-01`
3. Artifacts saved to `model/artifacts/industry_forecast/`
4. Use in backtest: `ibkr-trader backtest data.csv --strategy industry --model-artifact path/to/artifact`

## Common Pitfalls

1. **Mock data by default**: Set `IBKR_USE_MOCK_MARKET_DATA=false` to stream real prices (requires entitlements)
2. **Connection timeouts**: TWS/Gateway must be running before `ibkr-trader` commands
3. **Type narrowing**: After checking `isinstance()`, explicitly narrow types for mypy (see strategy.py:186)
4. **Decimal precision**: Use `Decimal(str(value))` when converting floats to avoid precision loss
5. **Event bus cleanup**: Always call `subscription.close()` or use `async with` context manager
6. **IBKR rate limits**: Historical data requests are throttled by config, watch for `SnapshotLimitError`

## Testing Guidelines

- Unit tests in `tests/` mirror `ibkr_trader/` structure
- Use `pytest-asyncio` for async test functions
- Mark slow tests (external API calls) with `@pytest.mark.slow`
- Safety tests (`test_safety.py`) must pass - these are critical
- Mock IBKR client for broker tests (see `test_broker.py`)
- Simulated broker for strategy tests (see `test_strategy.py`)

## Environment Variables

Key `.env` settings (all prefixed with `IBKR_`):
- `TRADING_MODE=paper|live` (default: paper)
- `PORT=7497` (paper) or `7496` (live)
- `MAX_POSITION_SIZE=100`
- `MAX_DAILY_LOSS=1000.0`
- `MAX_ORDER_EXPOSURE=10000.0`
- `USE_MOCK_MARKET_DATA=true`
- `TRAINING_DATA_SOURCE=yfinance|ibkr`
- `TRAINING_PRICE_CACHE_TTL=3600.0` (seconds, or null to disable)
- `TRAINING_OPTION_CACHE_TTL=3600.0`
- `TRAINING_MAX_SNAPSHOTS=50`
- `TRAINING_SNAPSHOT_INTERVAL=1.0`

## Dependencies

Core runtime:
- `ib-insync>=0.9.86` - IBKR TWS/Gateway client
- `pydantic>=2.0.0` - Runtime validation
- `typer>=0.9.0` - CLI framework
- `loguru>=0.7.0` - Logging
- `pandas>=2.0.0` - Data manipulation

Development:
- `pytest>=7.4.0`, `pytest-asyncio>=0.21.0` - Testing
- `mypy>=1.7.0` - Static type checking
- `ruff>=0.1.0` - Fast linter/formatter

Training (optional):
- `yfinance>=0.2.30` - Free market data
- `matplotlib>=3.8.0` - Visualization

## Additional Resources

- [Quick Start Guide](QUICKSTART.md) - Installation and first trades
- [Monitoring Guide](docs/monitoring_guide.md) - Real-time dashboards and session analysis
- [Model Training Guide](docs/model_training_guide.md) - Data caching and ML workflows
- [Strategy Guide](docs/unified_strategy_guide.md) - Complete strategy development reference
- [Order Book Implementation](docs/order_book_implementation.md) - L2 market depth integration
