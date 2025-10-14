"""Tests for market data storage helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from model.data.models import (
    BookSide,
    OptionRight,
    OptionSurfaceEntry,
    OrderBookLevel,
    OrderBookSnapshot,
    TradeEvent,
)
from model.data.storage import OptionSurfaceStore, OrderBookStore, TradeStore


def test_order_book_store_round_trip(tmp_path: Path) -> None:
    snapshot = OrderBookSnapshot(
        timestamp=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        symbol="AAPL",
        levels=[
            OrderBookLevel(side=BookSide.BID, price=199.5, size=100, level=1),
            OrderBookLevel(side=BookSide.ASK, price=200.5, size=120, level=1),
        ],
        venue="SMART",
    )

    store = OrderBookStore(tmp_path)
    path = store.append_snapshot(snapshot)
    frame = store.load_snapshots("AAPL", "20240101")

    assert path.exists()
    assert len(frame) == 2
    assert set(frame["side"]) == {"bid", "ask"}
    assert frame.iloc[0]["symbol"] == "AAPL"


def test_trade_store_round_trip(tmp_path: Path) -> None:
    events = [
        TradeEvent(
            timestamp=datetime(2024, 1, 1, 14, 0, 1, tzinfo=UTC),
            symbol="AAPL",
            price=200.0,
            size=50,
            side="buy",
        ),
        TradeEvent(
            timestamp=datetime(2024, 1, 1, 14, 0, 2, tzinfo=UTC),
            symbol="AAPL",
            price=200.1,
            size=25,
            side="sell",
        ),
    ]

    store = TradeStore(tmp_path)
    path = store.append_events(events)
    frame = store.load_events("AAPL", "20240101")

    assert path.exists()
    assert len(frame) == 2
    assert list(frame["price"]) == [200.0, 200.1]


def test_option_surface_store_round_trip(tmp_path: Path) -> None:
    entries = [
        OptionSurfaceEntry(
            timestamp=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
            symbol="AAPL",
            expiry="2024-01-19",
            strike=200.0,
            right=OptionRight.CALL,
            bid=1.2,
            ask=1.3,
            mid=1.25,
            implied_vol=0.35,
            underlying_price=199.8,
            source="test",
        ),
        OptionSurfaceEntry(
            timestamp=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
            symbol="AAPL",
            expiry="2024-01-19",
            strike=200.0,
            right=OptionRight.PUT,
            bid=1.1,
            ask=1.25,
            mid=1.175,
            implied_vol=0.34,
            underlying_price=199.8,
            source="test",
        ),
    ]

    store = OptionSurfaceStore(tmp_path)
    path = store.append_entries(entries)
    frame = store.load_entries("AAPL", "2024-01-19")

    assert path.exists()
    assert len(frame) == 2
    assert set(frame["right"]) == {"C", "P"}
    assert frame["symbol"].tolist() == ["AAPL", "AAPL"]
