"""High level client orchestrating cache + source interactions."""

from __future__ import annotations

import logging
from typing import Callable

import pandas as pd

from .cache_store import FileCacheStore
from .market_data import MarketDataSource, PriceBarRequest, normalize_price_columns

logger = logging.getLogger(__name__)

DEFAULT_NORMALIZER = normalize_price_columns


class MarketDataClient:
    """Fetch historical bars with optional caching and normalization."""

    def __init__(
        self,
        *,
        source: MarketDataSource,
        cache: FileCacheStore | None = None,
        normalizer: Callable[[pd.DataFrame], pd.DataFrame] | None = DEFAULT_NORMALIZER,
    ) -> None:
        self._source = source
        self._cache = cache
        self._normalizer = normalizer

    def get_price_bars(self, request: PriceBarRequest) -> pd.DataFrame:
        request_label = f"{request.symbol}:{request.start.isoformat()}->{request.end.isoformat()}"
        cached = self._cache.load_price_bars(request) if self._cache is not None else None
        if cached is not None:
            logger.debug("Returning cached price bars for %s", request_label)
            return cached.copy()

        frame = self._source.get_price_bars(request)
        if self._normalizer is not None:
            frame = self._normalizer(frame)

        if self._cache is not None:
            self._cache.store_price_bars(request, frame)
            logger.debug("Stored price bars in cache for %s (rows=%d)", request_label, len(frame))
        else:
            logger.debug("Fetched price bars without cache for %s (rows=%d)", request_label, len(frame))

        return frame.copy()
