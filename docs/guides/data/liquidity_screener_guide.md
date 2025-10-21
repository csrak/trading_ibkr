# Liquidity Screener Guide

**Last Updated:** 2025-10-21

## Overview

The `LiquidityScreener` filters symbols based on real market liquidity metrics (average dollar volume and price) calculated from historical bar data. It supports both real data from `MarketDataClient` and mock data for testing.

## Features

- **Real market data integration** via `MarketDataClient` with caching
- **Configurable thresholds** for minimum dollar volume and price
- **Automatic sorting** by dollar volume (most liquid first)
- **Fallback to mock data** when no client provided
- **Staleness tracking** for periodic refresh

## Quick Start

### Mock Mode (Testing)

```python
from ibkr_trader.data import LiquidityScreener, LiquidityScreenerConfig
from decimal import Decimal
import asyncio

# Create screener without market data client
config = LiquidityScreenerConfig(
    minimum_dollar_volume=Decimal("5000000"),  # $5M daily
    minimum_price=Decimal("10"),                # $10 minimum
    max_symbols=10,
)

screener = LiquidityScreener(config)
result = asyncio.run(screener.run())

print(f"Selected symbols: {result.symbols}")
print(f"Data source: {result.metadata['data_source']}")  # "mock"
```

### Real Data Mode (Production)

```python
from ibkr_trader.data import LiquidityScreener, LiquidityScreenerConfig
from model.data.client import MarketDataClient
from model.data.sources import YFinanceMarketDataSource
from model.data.cache_store import FileCacheStore
from decimal import Decimal
from pathlib import Path
import asyncio

# Create market data client with caching
source = YFinanceMarketDataSource()
cache = FileCacheStore(cache_dir=Path("data/cache"), ttl_seconds=3600)
client = MarketDataClient(source=source, cache=cache)

# Create screener with real data
config = LiquidityScreenerConfig(
    universe=["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA"],
    minimum_dollar_volume=Decimal("100000000"),  # $100M daily
    minimum_price=Decimal("50"),                  # $50 minimum
    lookback_days=20,                             # 20-day average
    max_symbols=5,
)

screener = LiquidityScreener(config, market_data_client=client)
result = asyncio.run(screener.run())

print(f"Top {len(result.symbols)} liquid symbols: {result.symbols}")
print(f"Data source: {result.metadata['data_source']}")  # "real"
```

## Configuration

### LiquidityScreenerConfig

```python
class LiquidityScreenerConfig(BaseModel):
    minimum_dollar_volume: Decimal = Decimal("5000000")
    # Minimum average daily dollar volume (price × volume)
    # Default: $5M

    minimum_price: Decimal = Decimal("5")
    # Minimum share price filter
    # Default: $5 (filters out penny stocks)

    universe: Sequence[str] = []
    # Optional: specific symbols to screen
    # If empty, uses default universe of 20 liquid US equities
    # Default: [] (use default universe)

    lookback_days: int = 20
    # Number of days for calculating averages
    # Default: 20 trading days (~1 month)

    max_symbols: int = 20
    # Maximum symbols to return
    # Default: 20
```

## How It Works

### Real Data Mode

When a `MarketDataClient` is provided:

1. **Fetch Historical Bars**: For each symbol in the universe, fetches `lookback_days` of daily OHLC+Volume data
2. **Calculate Metrics**:
   - **Average Price**: `mean(close_prices)`
   - **Average Dollar Volume**: `mean(close_price × volume)`
3. **Apply Filters**: Remove symbols below minimum thresholds
4. **Sort**: Order by dollar volume (descending)
5. **Limit**: Return top `max_symbols` results

### Mock Data Mode

When no `MarketDataClient` is provided:
- Uses synthetic data for testing
- Returns configured universe with simulated metrics
- Useful for strategy development without external API calls

## Integration with Strategies

### AdaptiveMomentumStrategy Example

```python
from ibkr_trader.strategies import AdaptiveMomentumStrategy, AdaptiveMomentumConfig
from ibkr_trader.data import LiquidityScreener, LiquidityScreenerConfig
from model.data.client import MarketDataClient
from model.data.sources import YFinanceMarketDataSource
from model.data.cache_store import FileCacheStore
from pathlib import Path
from decimal import Decimal

# Create screener
source = YFinanceMarketDataSource()
cache = FileCacheStore(cache_dir=Path("data/cache"), ttl_seconds=3600)
client = MarketDataClient(source=source, cache=cache)

screener_config = LiquidityScreenerConfig(
    minimum_dollar_volume=Decimal("50000000"),  # $50M
    minimum_price=Decimal("20"),
    lookback_days=30,
    max_symbols=10,
)

screener = LiquidityScreener(screener_config, market_data_client=client)

# Create strategy (without screener initially)
strategy_config = AdaptiveMomentumConfig(
    name="adaptive_momentum",
    symbols=[],  # Will be populated by screener
    position_size=10,
)

strategy = AdaptiveMomentumStrategy(
    config=strategy_config,
    broker=broker,
    event_bus=event_bus,
)

# Attach screener to strategy
strategy.set_screener(screener)

# Refresh universe before starting
await strategy.refresh_universe()

# Now strategy trades the most liquid symbols
await strategy.start()
```

## Data Sources

### YFinance (Recommended for Testing)

```python
from model.data.sources import YFinanceMarketDataSource

source = YFinanceMarketDataSource()
# Free, no API key required
# 1-day bars available for all US equities
# Rate-limited but generous
```

### IBKR Historical Data

```python
from model.data.ibkr import IBKRMarketDataSource
from ib_insync import IB

ib = IB()
await ib.connectAsync("127.0.0.1", 7497, clientId=2)

source = IBKRMarketDataSource(ib=ib)
# Requires IBKR account with market data entitlements
# Subject to snapshot limits (see config.IBKR_TRAINING_MAX_SNAPSHOTS)
# More accurate but rate-limited
```

## Caching

Enable caching to avoid repeated API calls:

```python
from model.data.cache_store import FileCacheStore
from pathlib import Path

cache = FileCacheStore(
    cache_dir=Path("data/cache"),
    ttl_seconds=3600,  # 1 hour
)

client = MarketDataClient(source=source, cache=cache)
# Subsequent screener runs within 1 hour use cached data
```

## Staleness Checking

Track when screener results become stale:

```python
from datetime import timedelta

result = await screener.run()

# Check if results are stale after 15 minutes
if screener.is_stale(timedelta(minutes=15)):
    # Refresh screener
    result = await screener.run()
```

## Default Universe

When `config.universe` is empty, the screener uses a default universe of 20 liquid US equities:

```
AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA, BRK.B, JPM, V,
UNH, JNJ, WMT, XOM, PG, MA, HD, CVX, MRK, ABBV
```

To override:

```python
config = LiquidityScreenerConfig(
    universe=["AAPL", "MSFT", "TSLA"],  # Custom universe
)
```

## Result Format

### ScreenerResult

```python
@dataclass
class ScreenerResult:
    symbols: Sequence[str]          # Filtered symbols (sorted by dollar volume)
    generated_at: datetime           # Timestamp when screener ran
    metadata: dict[str, object] | None

# Metadata keys:
# - "universe_size": int - Number of symbols returned
# - "lookback_days": int - Lookback period used
# - "stale": bool - Whether result is stale (always False)
# - "data_source": str - "real" or "mock"
```

## Error Handling

The screener gracefully handles errors:

```python
# Missing data for symbol
# Logs warning, skips symbol, continues

# Network error fetching data
# Logs warning, skips symbol, continues

# Invalid data format (missing close/volume columns)
# Logs warning, skips symbol, continues
```

If all symbols fail, returns empty result:

```python
result = await screener.run()
if not result.symbols:
    logger.warning("Screener returned no symbols")
    # Use fallback or raise alert
```

## Performance Considerations

### Batch Size

For large universes, fetching data can be slow:

```python
# Good: Small focused universe
config = LiquidityScreenerConfig(
    universe=["AAPL", "MSFT", "NVDA"],  # 3 symbols, fast
)

# Slower: Large universe
config = LiquidityScreenerConfig(
    universe=[...],  # 100+ symbols, slower
)
```

### Caching Strategy

```python
# Aggressive caching (1 day)
cache = FileCacheStore(ttl_seconds=86400)
# Use when: Screener runs frequently, data freshness not critical

# Conservative caching (15 minutes)
cache = FileCacheStore(ttl_seconds=900)
# Use when: Need fresher data, screener runs infrequently
```

### Rate Limiting

When using IBKR as data source:

```python
# Configure in .env
IBKR_TRAINING_MAX_SNAPSHOTS=50
IBKR_TRAINING_SNAPSHOT_INTERVAL=1.0  # seconds between requests

# Respect limits in code
universe = config.universe[:50]  # Limit to 50 symbols
```

## Testing

### Unit Tests

```bash
# Run liquidity screener tests
uv run pytest tests/test_liquidity_screener.py -v
```

### Integration Test

```python
# Test with real YFinance data
from model.data.sources import YFinanceMarketDataSource
from model.data.client import MarketDataClient
from ibkr_trader.data import LiquidityScreener, LiquidityScreenerConfig
from decimal import Decimal

source = YFinanceMarketDataSource()
client = MarketDataClient(source=source, cache=None)  # No cache for test

config = LiquidityScreenerConfig(
    universe=["AAPL", "MSFT"],
    minimum_dollar_volume=Decimal("1"),  # Low threshold
    minimum_price=Decimal("1"),
    lookback_days=5,
)

screener = LiquidityScreener(config, market_data_client=client)
result = await screener.run()

assert "AAPL" in result.symbols
assert "MSFT" in result.symbols
assert result.metadata["data_source"] == "real"
```

## Troubleshooting

### "No data available for SYMBOL"

**Cause**: Symbol not found or no trading activity in lookback period

**Solution**: Check symbol is valid, increase `lookback_days`, or remove from universe

### "Missing required columns for SYMBOL"

**Cause**: Data source returned unexpected column format

**Solution**: Check data source is returning normalized columns (`close`, `volume`)

### Screener returns empty result

**Causes**:
- All symbols filtered out by thresholds (too restrictive)
- Network error fetching data
- Invalid universe

**Solution**:
- Lower `minimum_dollar_volume` and `minimum_price`
- Check logs for warnings
- Verify universe contains valid symbols

## See Also

- [Model Training Guide](model_training_guide.md) - Data caching and sources
- [Adaptive Momentum Plan](../../plans/active/adaptive_momentum_plan.md) - Screener integration roadmap
- [Unified Strategy Guide](../strategies/unified_strategy_guide.md) - Strategy development patterns
