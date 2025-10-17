"""Filesystem backed cache for historical price bars."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import pandas as pd

from .constants import DEFAULT_CACHE_TTL_SECONDS
from .market_data import PriceBarRequest
from .utils import file_lock, write_csv_atomic

logger = logging.getLogger(__name__)


class FileCacheStore:
    """Persist price bar data frames to disk for reuse."""

    def __init__(self, base_dir: Path, *, ttl_seconds: float | None = None) -> None:
        self._base_dir = Path(base_dir)
        self._ttl_seconds = ttl_seconds

    def load_price_bars(self, request: PriceBarRequest) -> pd.DataFrame | None:
        path = self._path_for_request(request)
        if not path.exists():
            return None
        if self._is_expired(path):
            logger.debug("Cache expired for %s", path)
            return None
        logger.debug("Cache hit for %s", path)
        return pd.read_csv(path, index_col=0, parse_dates=True)

    def store_price_bars(self, request: PriceBarRequest, frame: pd.DataFrame) -> Path:
        path = self._path_for_request(request)
        path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(path):
            write_csv_atomic(path, frame, index=True)
        logger.debug("Cached price bars to %s", path)
        return path

    def _path_for_request(self, request: PriceBarRequest) -> Path:
        symbol = request.symbol.lower()
        interval = request.interval.replace("/", "_")
        key_source = f"{request.start.isoformat()}_{request.end.isoformat()}_{request.auto_adjust}"
        digest = hashlib.sha256(key_source.encode("utf-8")).hexdigest()[:16]
        directory = self._base_dir / symbol / interval
        filename = f"{digest}.csv"
        return directory / filename

    def _is_expired(self, path: Path) -> bool:
        if self._ttl_seconds is None:
            return False
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:  # pragma: no cover
            return True
        expired = (time.time() - mtime) > self._ttl_seconds
        if expired:
            logger.debug("Cache entry %s exceeded TTL %.2fs", path, self._ttl_seconds)
        return expired
