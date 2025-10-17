"""High level client orchestrating cache + source interactions."""

from __future__ import annotations

from typing import Callable

import pandas as pd

from .cache_store import FileCacheStore
from .market_data import MarketDataSource, PriceBarRequest, normalize_price_columns


class MarketDataClient:
    """Fetch historical bars with optional caching and normalization."""

    def __init__(
        self,
        *,
        source: MarketDataSource,
        cache: FileCacheStore | None = None,
        normalizer: Callable[[pd.DataFrame], pd.DataFrame] | None = normalize_price_columns,
    ) -> None:
        self._source = source
        self._cache = cache
        self._normalizer = normalizer

    def get_price_bars(self, request: PriceBarRequest) -> pd.DataFrame:
        cached = self._cache.load_price_bars(request) if self._cache is not None else None
        if cached is not None:
            return cached.copy()

        frame = self._source.get_price_bars(request)
        if self._normalizer is not None:
            frame = self._normalizer(frame)

        if self._cache is not None:
            self._cache.store_price_bars(request, frame)

        return frame.copy()
