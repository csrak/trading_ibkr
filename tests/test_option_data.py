"""Tests for option chain data utilities."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from model.data.constants import OPTION_CHAIN_METADATA_FILENAME
from model.data.options import (
    OptionChain,
    OptionChainCacheStore,
    OptionChainClient,
    OptionChainRequest,
    OptionChainSource,
)
from model.data.sources import YFinanceOptionChainSource


def sample_chain() -> OptionChain:
    calls = pd.DataFrame(
        {
            "contractSymbol": ["AAPL240119C00150000"],
            "bid": [1.25],
            "ask": [1.35],
        }
    )
    puts = pd.DataFrame(
        {
            "contractSymbol": ["AAPL240119P00150000"],
            "bid": [1.10],
            "ask": [1.20],
        }
    )
    return OptionChain(calls=calls, puts=puts)


def test_option_chain_cache_roundtrip(tmp_path: Path) -> None:
    cache = OptionChainCacheStore(tmp_path)
    request = OptionChainRequest(symbol="AAPL", expiry=datetime(2024, 1, 19, tzinfo=UTC))
    chain = sample_chain()

    cache.store_option_chain(request, chain)
    reloaded = cache.load_option_chain(request)

    assert reloaded is not None
    pd.testing.assert_frame_equal(reloaded.calls, chain.calls)
    pd.testing.assert_frame_equal(reloaded.puts, chain.puts)


class DummyOptionSource(OptionChainSource):
    def __init__(self, chain: OptionChain) -> None:
        self.chain = chain
        self.calls = 0

    def get_option_chain(self, request: OptionChainRequest) -> OptionChain:
        self.calls += 1
        return self.chain


def test_option_chain_client_uses_cache(tmp_path: Path) -> None:
    chain = sample_chain()
    source = DummyOptionSource(chain)
    cache = OptionChainCacheStore(tmp_path)
    client = OptionChainClient(source=source, cache=cache)
    request = OptionChainRequest(symbol="AAPL", expiry=datetime(2024, 1, 19, tzinfo=UTC))

    first = client.get_option_chain(request)
    second = client.get_option_chain(request)

    assert source.calls == 1
    pd.testing.assert_frame_equal(first.calls, chain.calls)
    pd.testing.assert_frame_equal(second.puts, chain.puts)


def test_yfinance_option_chain_source(monkeypatch: pytest.MonkeyPatch) -> None:
    chain = sample_chain()
    fake_chain = SimpleNamespace(calls=chain.calls, puts=chain.puts)

    class FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def option_chain(self, expiry: str) -> SimpleNamespace:
            assert expiry == "2024-01-19"
            return fake_chain

    monkeypatch.setattr("yfinance.Ticker", FakeTicker)

    source = YFinanceOptionChainSource()
    request = OptionChainRequest(symbol="AAPL", expiry=datetime(2024, 1, 19, tzinfo=UTC))
    result = source.get_option_chain(request)

    pd.testing.assert_frame_equal(result.calls, chain.calls)
    pd.testing.assert_frame_equal(result.puts, chain.puts)


def test_option_chain_cache_ttl(tmp_path: Path) -> None:
    cache = OptionChainCacheStore(tmp_path, max_age_seconds=60)
    request = OptionChainRequest(symbol="AAPL", expiry=datetime(2024, 1, 19, tzinfo=UTC))
    chain = sample_chain()

    calls_path, _ = cache.store_option_chain(request, chain)
    metadata_path = calls_path.parent / OPTION_CHAIN_METADATA_FILENAME
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["stored_at"] = time.time() - 3600
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    assert cache.load_option_chain(request) is None


def test_option_chain_metadata_entries(tmp_path: Path) -> None:
    cache = OptionChainCacheStore(tmp_path)
    request = OptionChainRequest(symbol="AAPL", expiry=datetime(2024, 1, 19, tzinfo=UTC))
    chain = sample_chain()
    cache.store_option_chain(request, chain)

    entries = cache.metadata_entries()
    assert entries, "Expected metadata entries to be reported"
    assert any(entry["symbol"] == "AAPL" for entry in entries)
