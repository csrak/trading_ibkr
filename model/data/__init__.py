"""Public exports for the `model.data` package.

The package provides a small collection of utilities that power market data
ingestion, caching, and storage for backtests, simulations, and model
training.  Modules are intentionally lightweight and rely on pandas for data
manipulation so they can be reused both in production code and tests.
"""

from __future__ import annotations

from .cache_store import FileCacheStore
from .client import MarketDataClient
from .ibkr import IBKRMarketDataSource, IBKROptionChainSource, SnapshotLimitError
from .options import (
    OptionChain,
    OptionChainCacheStore,
    OptionChainClient,
    OptionChainRequest,
    OptionChainSource,
)
from .sources import (
    YFinanceMarketDataSource,
    YFinanceOptionChainSource,
)

__all__ = [
    "FileCacheStore",
    "MarketDataClient",
    "IBKRMarketDataSource",
    "IBKROptionChainSource",
    "SnapshotLimitError",
    "OptionChain",
    "OptionChainCacheStore",
    "OptionChainClient",
    "OptionChainRequest",
    "OptionChainSource",
    "YFinanceMarketDataSource",
    "YFinanceOptionChainSource",
]
