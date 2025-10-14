"""Tests for the IBKR market data source implementation."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from model.data.ibkr import IBKRMarketDataSource, SnapshotLimitError
from model.data.market_data import PriceBarRequest


class DummyIB:
    def __init__(self, bars: list[object]) -> None:
        self.bars = bars
        self.connected = True
        self.requests: list[dict[str, object]] = []

    def isConnected(self) -> bool:  # noqa: N802 - mimics ib_insync API
        return self.connected

    def connect(self, *args: object, **kwargs: object) -> None:  # pragma: no cover - not used
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def reqHistoricalData(self, contract: object, **kwargs: object) -> list[object]:  # noqa: N802
        self.requests.append({"contract": contract, **kwargs})
        return self.bars


def sample_bars() -> list[object]:
    return [
        SimpleNamespace(
            date=datetime(2024, 1, 1, tzinfo=UTC),
            open=100.0,
            high=105.0,
            low=95.0,
            close=102.5,
            volume=1_000,
            average=100.5,
            barCount=42,
        ),
        SimpleNamespace(
            date=datetime(2024, 1, 2, tzinfo=UTC),
            open=102.5,
            high=106.0,
            low=98.0,
            close=104.0,
            volume=1_200,
            average=102.0,
            barCount=35,
        ),
    ]


def test_ibkr_source_returns_dataframe() -> None:
    ib = DummyIB(sample_bars())
    source = IBKRMarketDataSource(
        ib=ib,
        contract_factory=lambda symbol: {"symbol": symbol},
        max_snapshots_per_session=5,
        min_request_interval_seconds=0.0,
    )

    request = PriceBarRequest(
        symbol="AAPL",
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 1, 3, tzinfo=UTC),
        interval="1d",
        auto_adjust=False,
    )
    frame = source.get_price_bars(request)

    assert list(frame.columns) == ["open", "high", "low", "close", "volume", "average", "bar_count"]
    assert frame.index.tz is not None
    assert frame.loc["2024-01-01", "close"] == pytest.approx(102.5)
    assert ib.requests, "Expected reqHistoricalData to be invoked"
    assert ib.requests[0]["durationStr"] == "2 D"
    assert ib.requests[0]["barSizeSetting"] == "1 day"


def test_ibkr_source_enforces_snapshot_limit() -> None:
    ib = DummyIB(sample_bars())
    source = IBKRMarketDataSource(
        ib=ib,
        contract_factory=lambda symbol: {"symbol": symbol},
        max_snapshots_per_session=1,
        min_request_interval_seconds=0.0,
    )

    request = PriceBarRequest(
        symbol="MSFT",
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 1, 2, tzinfo=UTC),
        interval="1d",
    )

    source.get_price_bars(request)

    with pytest.raises(SnapshotLimitError):
        source.get_price_bars(request)
