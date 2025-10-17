"""Utilities for persisting structured market data to disk."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from .models import OptionSurfaceEntry, OrderBookSnapshot, TradeEvent


class OrderBookStore:
    """Store order book snapshots to CSV."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)

    def append_snapshot(self, snapshot: OrderBookSnapshot) -> Path:
        directory = self._base_dir / snapshot.symbol.lower()
        directory.mkdir(parents=True, exist_ok=True)
        file_path = directory / f"{snapshot.timestamp:%Y%m%d}.csv"
        frame = pd.DataFrame([level.to_record(snapshot.timestamp, snapshot.symbol, snapshot.venue) for level in snapshot.levels])
        frame.to_csv(file_path, mode="a", header=not file_path.exists(), index=False)
        return file_path

    def load_snapshots(self, symbol: str, date_label: str) -> pd.DataFrame:
        file_path = self._base_dir / symbol.lower() / f"{date_label}.csv"
        return pd.read_csv(file_path)


class TradeStore:
    """Store trade prints to CSV."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)

    def append_events(self, events: Iterable[TradeEvent]) -> Path:
        events = list(events)
        if not events:
            raise ValueError("No trade events provided")
        symbol = events[0].symbol.lower()
        directory = self._base_dir / symbol
        directory.mkdir(parents=True, exist_ok=True)
        file_path = directory / f"{events[0].timestamp:%Y%m%d}.csv"
        frame = pd.DataFrame([event.to_record() for event in events])
        frame.to_csv(file_path, mode="a", header=not file_path.exists(), index=False)
        return file_path

    def load_events(self, symbol: str, date_label: str) -> pd.DataFrame:
        file_path = self._base_dir / symbol.lower() / f"{date_label}.csv"
        return pd.read_csv(file_path)


class OptionSurfaceStore:
    """Append option surface snapshots."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)

    def append_entries(self, entries: Iterable[OptionSurfaceEntry]) -> Path:
        rows = [entry.to_record() for entry in entries]
        if not rows:
            raise ValueError("No option surface entries provided")
        symbol = rows[0]["symbol"].lower()
        expiry = rows[0]["expiry"]
        directory = self._base_dir / symbol / expiry
        directory.mkdir(parents=True, exist_ok=True)
        file_path = directory / "surface.csv"
        frame = pd.DataFrame(rows)
        frame.to_csv(file_path, mode="a", header=not file_path.exists(), index=False)
        return file_path

    def load_entries(self, symbol: str, expiry: str) -> pd.DataFrame:
        file_path = self._base_dir / symbol.lower() / expiry / "surface.csv"
        return pd.read_csv(file_path)
