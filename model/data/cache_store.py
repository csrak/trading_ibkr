"""Filesystem backed cache for historical price bars."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Callable

import pandas as pd

from .constants import DEFAULT_CACHE_TTL_SECONDS, CACHE_STALENESS_WARNING_FRACTION
from .market_data import PriceBarRequest
from .utils import file_lock, write_csv_atomic

logger = logging.getLogger(__name__)


class FileCacheStore:
    """Persist price bar data frames to disk for reuse."""

    def __init__(
        self,
        base_dir: Path,
        *,
        ttl_seconds: float | None = DEFAULT_CACHE_TTL_SECONDS,
        warning_handler: Callable[[str, dict[str, object] | None], None] | None = None,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._ttl_seconds = ttl_seconds
        self._warning_handler = warning_handler

    def load_price_bars(self, request: PriceBarRequest) -> pd.DataFrame | None:
        path = self._path_for_request(request)
        if not path.exists():
            return None
        if self._is_expired(path):
            logger.debug("Cache expired for %s", path)
            return None
        self._warn_if_stale(path)
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

    @property
    def ttl_seconds(self) -> float | None:
        return self._ttl_seconds

    def _age_seconds(self, path: Path) -> float | None:
        try:
            return time.time() - path.stat().st_mtime
        except FileNotFoundError:  # pragma: no cover
            return None

    def _warn_if_stale(self, path: Path) -> None:
        if self._ttl_seconds is None:
            return
        age = self._age_seconds(path)
        if age is None:
            return
        if age >= self._ttl_seconds * CACHE_STALENESS_WARNING_FRACTION:
            logger.warning(
                "Price cache entry %s is getting stale (age=%.0fs ttl=%.0fs)",
                path,
                age,
                self._ttl_seconds,
            )
            if self._warning_handler is not None:
                self._warning_handler(
                    "Price cache entry nearing TTL",
                    {"path": str(path), "age_seconds": age, "ttl_seconds": self._ttl_seconds},
                )
