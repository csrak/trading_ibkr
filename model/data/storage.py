"""Utilities for persisting structured market data to disk."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import pandas as pd

from .constants import (
    OPTION_SURFACE_FILENAME,
    OPTION_SURFACE_SCHEMA_VERSION,
    ORDER_BOOK_FILENAME_TEMPLATE,
    ORDER_BOOK_SCHEMA_VERSION,
    SCHEMA_VERSION_FIELD,
    TRADE_FILENAME_TEMPLATE,
    TRADE_SCHEMA_VERSION,
)
from .models import OptionSurfaceEntry, OrderBookSnapshot, TradeEvent
from .utils import file_lock, write_csv_atomic

logger = logging.getLogger(__name__)


class OrderBookStore:
    """Store order book snapshots to CSV."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)

    def append_snapshot(self, snapshot: OrderBookSnapshot) -> Path:
        directory = self._base_dir / snapshot.symbol.lower()
        directory.mkdir(parents=True, exist_ok=True)
        date_label = f"{snapshot.timestamp:%Y%m%d}"
        file_path = directory / ORDER_BOOK_FILENAME_TEMPLATE.format(date_label=date_label)

        records = [
            level.to_record(snapshot.timestamp, snapshot.symbol, snapshot.venue)
            for level in snapshot.levels
        ]
        for record in records:
            record[SCHEMA_VERSION_FIELD] = ORDER_BOOK_SCHEMA_VERSION
        new_frame = pd.DataFrame(records)
        if not new_frame.empty:
            new_frame[SCHEMA_VERSION_FIELD] = ORDER_BOOK_SCHEMA_VERSION

        with file_lock(file_path):
            combined = (
                pd.concat([_read_with_schema(file_path), new_frame], ignore_index=True)
                if file_path.exists()
                else new_frame
            )
            write_csv_atomic(file_path, combined, index=False)
        logger.debug(
            "Stored order book snapshot for %s date=%s (levels=%d)",
            snapshot.symbol,
            date_label,
            len(new_frame),
        )
        return file_path

    def load_snapshots(self, symbol: str, date_label: str) -> pd.DataFrame:
        file_path = self._base_dir / symbol.lower() / ORDER_BOOK_FILENAME_TEMPLATE.format(
            date_label=date_label
        )
        frame = _read_with_schema(file_path)
        _validate_schema(frame, ORDER_BOOK_SCHEMA_VERSION)
        logger.debug("Loaded order book snapshots for %s date=%s", symbol, date_label)
        return frame


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
        date_label = f"{events[0].timestamp:%Y%m%d}"
        file_path = directory / TRADE_FILENAME_TEMPLATE.format(date_label=date_label)

        records = [event.to_record() for event in events]
        for record in records:
            record[SCHEMA_VERSION_FIELD] = TRADE_SCHEMA_VERSION
        new_frame = pd.DataFrame(records)
        if not new_frame.empty:
            new_frame[SCHEMA_VERSION_FIELD] = TRADE_SCHEMA_VERSION

        with file_lock(file_path):
            combined = (
                pd.concat([_read_with_schema(file_path), new_frame], ignore_index=True)
                if file_path.exists()
                else new_frame
            )
            write_csv_atomic(file_path, combined, index=False)
        logger.debug(
            "Stored %d trade events for %s date=%s", len(new_frame), symbol.upper(), date_label
        )
        return file_path

    def load_events(self, symbol: str, date_label: str) -> pd.DataFrame:
        file_path = self._base_dir / symbol.lower() / TRADE_FILENAME_TEMPLATE.format(
            date_label=date_label
        )
        frame = _read_with_schema(file_path)
        _validate_schema(frame, TRADE_SCHEMA_VERSION)
        logger.debug("Loaded trade events for %s date=%s", symbol, date_label)
        return frame


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
        file_path = directory / OPTION_SURFACE_FILENAME

        for row in rows:
            row[SCHEMA_VERSION_FIELD] = OPTION_SURFACE_SCHEMA_VERSION
        new_frame = pd.DataFrame(rows)
        if not new_frame.empty:
            new_frame[SCHEMA_VERSION_FIELD] = OPTION_SURFACE_SCHEMA_VERSION

        with file_lock(file_path):
            combined = (
                pd.concat([_read_with_schema(file_path), new_frame], ignore_index=True)
                if file_path.exists()
                else new_frame
            )
            write_csv_atomic(file_path, combined, index=False)
        logger.debug(
            "Stored option surface entries for %s expiry=%s (%d rows)",
            symbol.upper(),
            expiry,
            len(new_frame),
        )
        return file_path

    def load_entries(self, symbol: str, expiry: str) -> pd.DataFrame:
        file_path = self._base_dir / symbol.lower() / expiry / OPTION_SURFACE_FILENAME
        frame = _read_with_schema(file_path)
        _validate_schema(frame, OPTION_SURFACE_SCHEMA_VERSION)
        logger.debug("Loaded option surface entries for %s expiry=%s", symbol, expiry)
        return frame


def _validate_schema(frame: pd.DataFrame, expected_version: str) -> None:
    schema_series = frame.get(SCHEMA_VERSION_FIELD)
    if schema_series is None:
        raise ValueError("Missing schema information in stored dataset")
    invalid_mask = schema_series.astype(str) != expected_version
    if invalid_mask.any():
        raise ValueError(
            f"Incompatible schema version detected (expected {expected_version})"
        )


def _read_with_schema(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype={SCHEMA_VERSION_FIELD: str})
