"""Filesystem backed cache for historical price bars."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from .market_data import PriceBarRequest


class FileCacheStore:
    """Persist price bar data frames to disk for reuse."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)

    def load_price_bars(self, request: PriceBarRequest) -> pd.DataFrame | None:
        path = self._path_for_request(request)
        if not path.exists():
            return None
        return pd.read_csv(path, index_col=0, parse_dates=True)

    def store_price_bars(self, request: PriceBarRequest, frame: pd.DataFrame) -> Path:
        path = self._path_for_request(request)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        frame.to_csv(temp)
        temp.replace(path)
        return path

    def _path_for_request(self, request: PriceBarRequest) -> Path:
        symbol = request.symbol.lower()
        interval = request.interval.replace("/", "_")
        key_source = f"{request.start.isoformat()}_{request.end.isoformat()}_{request.auto_adjust}"
        digest = hashlib.sha256(key_source.encode("utf-8")).hexdigest()[:16]
        directory = self._base_dir / symbol / interval
        filename = f"{digest}.csv"
        return directory / filename
