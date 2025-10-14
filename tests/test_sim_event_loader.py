"""Tests for simulation event loader and mock broker."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from ibkr_trader.events import EventBus, EventTopic, ExecutionEvent
from ibkr_trader.models import OrderRequest, OrderSide, OrderStatus, OrderType, SymbolContract
from ibkr_trader.sim.events import EventLoader
from ibkr_trader.sim.mock_broker import MockBroker
from ibkr_trader.sim.runner import ReplayRunner, ReplayStrategy
from ibkr_trader.sim.strategies import FixedSpreadMMStrategy
from model.data.models import OrderBookSnapshot


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def test_event_loader_merges_streams(tmp_path: Path) -> None:
    order_book_path = tmp_path / "orderbook.csv"
    trade_path = tmp_path / "trades.csv"
    surface_path = tmp_path / "surface.csv"

    _write_csv(
        order_book_path,
        pd.DataFrame(
            [
                {
                    "timestamp": "2024-01-01T14:00:00+00:00",
                    "symbol": "AAPL",
                    "side": "bid",
                    "price": 199.5,
                    "size": 100,
                    "level": 1,
                },
                {
                    "timestamp": "2024-01-01T14:00:00+00:00",
                    "symbol": "AAPL",
                    "side": "ask",
                    "price": 200.5,
                    "size": 120,
                    "level": 1,
                },
            ]
        ),
    )

    _write_csv(
        trade_path,
        pd.DataFrame(
            [
                {
                    "timestamp": "2024-01-01T14:00:01+00:00",
                    "symbol": "AAPL",
                    "price": 200.0,
                    "size": 50,
                    "side": "buy",
                }
            ]
        ),
    )

    _write_csv(
        surface_path,
        pd.DataFrame(
            [
                {
                    "timestamp": "2024-01-01T14:00:02+00:00",
                    "symbol": "AAPL",
                    "expiry": "2024-01-19",
                    "strike": 200.0,
                    "right": "C",
                    "bid": 1.2,
                    "ask": 1.3,
                }
            ]
        ),
    )

    loader = EventLoader(
        order_book_files=[order_book_path],
        trade_files=[trade_path],
        option_surface_files=[surface_path],
    )

    events = list(loader.load_events())
    assert len(events) == 3  # snapshot, trade, option entry
    assert events[0].timestamp == datetime(2024, 1, 1, 14, 0, tzinfo=UTC)
    assert events[1].timestamp == datetime(2024, 1, 1, 14, 0, 1, tzinfo=UTC)
    assert events[2].timestamp == datetime(2024, 1, 1, 14, 0, 2, tzinfo=UTC)


@pytest.mark.asyncio
async def test_mock_broker_publish_fill() -> None:
    bus = EventBus()
    broker = MockBroker(bus)

    order_request = OrderRequest(
        contract=SymbolContract(symbol="AAPL"),
        side=OrderSide.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100"),
    )

    result = await broker.submit_limit_order(order_request)
    assert result.status == OrderStatus.SUBMITTED

    async with bus.subscribe(EventTopic.EXECUTION) as sub:
        await broker.simulate_fill(
            order_id=result.order_id,
            fill_quantity=1,
            fill_price=Decimal("100"),
        )
        execution_event = await asyncio.wait_for(sub.get(), timeout=1.0)

    assert isinstance(execution_event, ExecutionEvent)
    assert execution_event.quantity == 1
    assert execution_event.price == Decimal("100")


@pytest.mark.asyncio
async def test_replay_runner_dispatches_events(tmp_path: Path) -> None:
    order_book_path = tmp_path / "orderbook.csv"
    _write_csv(
        order_book_path,
        pd.DataFrame(
            [
                {
                    "timestamp": "2024-01-01T14:00:00+00:00",
                    "symbol": "AAPL",
                    "side": "bid",
                    "price": 199.5,
                    "size": 100,
                    "level": 1,
                }
            ]
        ),
    )

    class RecordingStrategy(ReplayStrategy):
        def __init__(self) -> None:
            self.order_book_events: list[str] = []

        async def on_order_book(self, snapshot: OrderBookSnapshot, broker: MockBroker) -> None:  # type: ignore[override]
            self.order_book_events.append(snapshot.symbol)

    strategy = RecordingStrategy()
    loader = EventLoader(order_book_files=[order_book_path])
    runner = ReplayRunner(loader=loader, strategy=strategy)

    await runner.run()

    assert strategy.order_book_events == ["AAPL"]


@pytest.mark.asyncio
async def test_fixed_spread_strategy_quotes_and_updates_inventory(tmp_path: Path) -> None:
    order_book_path = tmp_path / "orderbook.csv"
    _write_csv(
        order_book_path,
        pd.DataFrame(
            [
                {
                    "timestamp": "2024-01-01T14:00:00+00:00",
                    "symbol": "AAPL",
                    "side": "bid",
                    "price": 199.5,
                    "size": 100,
                    "level": 1,
                },
                {
                    "timestamp": "2024-01-01T14:00:00+00:00",
                    "symbol": "AAPL",
                    "side": "ask",
                    "price": 200.5,
                    "size": 100,
                    "level": 1,
                },
            ]
        ),
    )

    class AutoFillStrategy(FixedSpreadMMStrategy):
        async def on_order_book(self, snapshot: OrderBookSnapshot, broker: MockBroker) -> None:  # type: ignore[override]
            await super().on_order_book(snapshot, broker)
            if self.active_bid_id is not None and not hasattr(self, "_filled"):
                self._filled = True
                await broker.simulate_fill(
                    order_id=self.active_bid_id,
                    fill_quantity=self.quote_size,
                    fill_price=Decimal("199.4"),
                )

    strategy = AutoFillStrategy(symbol="AAPL", quote_size=1, spread=0.2, inventory_limit=2)
    loader = EventLoader(order_book_files=[order_book_path])
    runner = ReplayRunner(loader=loader, strategy=strategy)

    await runner.run()

    assert strategy.active_bid_id is not None
    assert strategy.active_ask_id is not None
    assert strategy.inventory == 1
