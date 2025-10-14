'"""Tests for market data abstractions and caching."""'

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from model.data.cache_store import FileCacheStore
from model.data.client import MarketDataClient
from model.data.market_data import MarketDataSource, PriceBarRequest
from model.data.sources import YFinanceMarketDataSource


class DummySource(MarketDataSource):
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.calls = 0

    def get_price_bars(self, request: PriceBarRequest) -> pd.DataFrame:
        self.calls += 1
        return self.frame


def sample_frame() -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    return pd.DataFrame({"open": [1, 2, 3], "close": [1.5, 2.5, 3.5]}, index=idx)


def test_file_cache_store_roundtrip(tmp_path: Path) -> None:
    cache = FileCacheStore(tmp_path)
    request = PriceBarRequest(
        symbol="AAPL",
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 1, 4, tzinfo=UTC),
    )
    frame = sample_frame()

    cache.store_price_bars(request, frame)
    reloaded = cache.load_price_bars(request)

    assert reloaded is not None
    pd.testing.assert_frame_equal(reloaded, frame, check_freq=False)


def test_market_data_client_uses_cache(tmp_path: Path) -> None:
    request = PriceBarRequest(
        symbol="MSFT",
        start=datetime(2024, 2, 1, tzinfo=UTC),
        end=datetime(2024, 2, 3, tzinfo=UTC),
    )
    frame = sample_frame()
    source = DummySource(frame)
    cache = FileCacheStore(tmp_path)
    client = MarketDataClient(source=source, cache=cache)

    result_first = client.get_price_bars(request)
    result_second = client.get_price_bars(request)

    assert source.calls == 1
    pd.testing.assert_frame_equal(result_first, frame, check_freq=False)
    pd.testing.assert_frame_equal(result_second, frame, check_freq=False)


def test_yfinance_source_normalizes_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    request = PriceBarRequest(
        symbol="GOOG",
        start=datetime(2024, 3, 1, tzinfo=UTC),
        end=datetime(2024, 3, 10, tzinfo=UTC),
    )

    multi_index_frame = pd.DataFrame(
        {
            ("Adj Close", "GOOG"): [100.0, 101.0],
            ("Volume", "GOOG"): [10, 20],
        },
        index=pd.date_range("2024-03-01", periods=2, freq="D"),
    )

    def fake_download(*args: object, **kwargs: object) -> pd.DataFrame:
        return multi_index_frame

    monkeypatch.setattr("yfinance.download", fake_download)
    source = YFinanceMarketDataSource()
    frame = source.get_price_bars(request)

    assert "adj_close_goog" in frame.columns
    assert "volume_goog" in frame.columns
    assert not frame.empty
