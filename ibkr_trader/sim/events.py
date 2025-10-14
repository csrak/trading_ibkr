"""Event replay utilities for market making simulations."""

from __future__ import annotations

import heapq
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from model.data.models import (
    BookSide,
    OptionRight,
    OptionSurfaceEntry,
    OrderBookLevel,
    OrderBookSnapshot,
    TradeEvent,
)


@dataclass(slots=True)
class ReplayEvent:
    timestamp: datetime
    payload: object


class EventLoader:
    """Loads depth, trade, and option surface data into replayable events."""

    def __init__(
        self,
        order_book_files: Sequence[Path] | None = None,
        trade_files: Sequence[Path] | None = None,
        option_surface_files: Sequence[Path] | None = None,
    ) -> None:
        self.order_book_files = list(order_book_files or [])
        self.trade_files = list(trade_files or [])
        self.option_surface_files = list(option_surface_files or [])

    def load_events(self) -> Iterator[ReplayEvent]:
        queue: list[tuple[datetime, int, object]] = []

        def enqueue(events: Iterator[ReplayEvent]) -> None:
            for sequence_id, event in enumerate(events):
                heapq.heappush(queue, (event.timestamp, sequence_id, event.payload))

        if self.order_book_files:
            enqueue(self._load_order_books())
        if self.trade_files:
            enqueue(self._load_trades())
        if self.option_surface_files:
            enqueue(self._load_option_surfaces())

        while queue:
            timestamp, _, payload = heapq.heappop(queue)
            yield ReplayEvent(timestamp=timestamp, payload=payload)

    def _load_order_books(self) -> Iterator[ReplayEvent]:
        for file_path in self.order_book_files:
            frame = pd.read_csv(file_path)
            required = {"timestamp", "symbol", "side", "price", "size", "level"}
            if not required.issubset(frame.columns):
                raise ValueError(f"{file_path} missing required columns {required}")

            grouped = frame.groupby("timestamp")
            for timestamp, group in grouped:
                levels = [
                    OrderBookLevel(
                        side=BookSide(level["side"]),
                        price=float(level["price"]),
                        size=float(level["size"]),
                        level=int(level["level"]),
                        num_orders=int(level["num_orders"])
                        if "num_orders" in level and pd.notna(level["num_orders"])
                        else None,
                    )
                    for level in group.to_dict("records")
                ]
                yield ReplayEvent(
                    timestamp=datetime.fromisoformat(timestamp),
                    payload=OrderBookSnapshot(
                        timestamp=datetime.fromisoformat(timestamp),
                        symbol=str(group.iloc[0]["symbol"]),
                        levels=levels,
                        venue=str(group.iloc[0].get("venue", "")) or None,
                    ),
                )

    def _load_trades(self) -> Iterator[ReplayEvent]:
        for file_path in self.trade_files:
            frame = pd.read_csv(file_path)
            required = {"timestamp", "symbol", "price", "size", "side"}
            if not required.issubset(frame.columns):
                raise ValueError(f"{file_path} missing required columns {required}")
            for record in frame.to_dict("records"):
                yield ReplayEvent(
                    timestamp=datetime.fromisoformat(record["timestamp"]),
                    payload=TradeEvent(
                        timestamp=datetime.fromisoformat(record["timestamp"]),
                        symbol=str(record["symbol"]),
                        price=float(record["price"]),
                        size=float(record["size"]),
                        side=str(record["side"]),
                        venue=str(record.get("venue", "")) or None,
                    ),
                )

    def _load_option_surfaces(self) -> Iterator[ReplayEvent]:
        for file_path in self.option_surface_files:
            frame = pd.read_csv(file_path)
            required = {"timestamp", "symbol", "expiry", "strike", "right", "bid", "ask"}
            if not required.issubset(frame.columns):
                raise ValueError(f"{file_path} missing required columns {required}")
            for record in frame.to_dict("records"):
                yield ReplayEvent(
                    timestamp=datetime.fromisoformat(record["timestamp"]),
                    payload=OptionSurfaceEntry(
                        timestamp=datetime.fromisoformat(record["timestamp"]),
                        symbol=str(record["symbol"]),
                        expiry=str(record["expiry"]),
                        strike=float(record["strike"]),
                        right=OptionRight(str(record["right"])),
                        bid=float(record["bid"]),
                        ask=float(record["ask"]),
                        mid=float(record["mid"])
                        if "mid" in record and pd.notna(record["mid"])
                        else None,
                        last=float(record["last"])
                        if "last" in record and pd.notna(record["last"])
                        else None,
                        implied_vol=float(record["implied_vol"])
                        if "implied_vol" in record and pd.notna(record["implied_vol"])
                        else None,
                        delta=float(record["delta"])
                        if "delta" in record and pd.notna(record["delta"])
                        else None,
                        gamma=float(record["gamma"])
                        if "gamma" in record and pd.notna(record["gamma"])
                        else None,
                        vega=float(record["vega"])
                        if "vega" in record and pd.notna(record["vega"])
                        else None,
                        theta=float(record["theta"])
                        if "theta" in record and pd.notna(record["theta"])
                        else None,
                        underlying_price=float(record["underlying_price"])
                        if "underlying_price" in record and pd.notna(record["underlying_price"])
                        else None,
                        source=str(record.get("source", "")) or None,
                    ),
                )
