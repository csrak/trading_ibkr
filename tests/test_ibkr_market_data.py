"""Tests for the IBKR market data source implementation."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from model.data.ibkr import IBKRMarketDataSource, IBKROptionChainSource, SnapshotLimitError
from model.data.market_data import PriceBarRequest
from model.data.options import OptionChainRequest


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


class DummyOptionIB(DummyIB):
    def __init__(self) -> None:
        super().__init__([])
        self.option_params = [
            SimpleNamespace(
                expirations={"20240119", "20240216"},
                strikes={95.0, 100.0, 105.0},
            )
        ]
        self.ticker_map: dict[str, SimpleNamespace] = {}
        self.qualify_calls: list[tuple[object, ...]] = []

    def reqSecDefOptParams(self, *args: object, **kwargs: object) -> list[object]:  # noqa: N802
        return self.option_params

    def qualifyContracts(self, *contracts: object) -> None:  # noqa: N802
        self.qualify_calls.append(contracts)

    def reqTickers(self, *contracts: object) -> list[SimpleNamespace]:  # type: ignore[override]  # noqa: N802
        if not contracts:
            return [
                SimpleNamespace(
                    marketPrice=lambda: 102.0,
                    last=101.5,
                    bid=101.0,
                    ask=102.0,
                )
            ]
        tickers = []
        for contract in contracts:
            right = getattr(contract, "right", "C")
            strike = getattr(contract, "strike", 100.0)
            key = f"{right}_{strike}"
            ticker = self.ticker_map.get(
                key,
                SimpleNamespace(
                    bid=1.0 if right == "C" else 0.9,
                    ask=1.2 if right == "C" else 1.1,
                    last=1.1 if right == "C" else 1.0,
                ),
            )
            tickers.append(ticker)
        return tickers


class SimpleOption:
    def __init__(self, symbol: str, expiry: str, strike: float, right: str) -> None:
        self.symbol = symbol
        self.expiry = expiry
        self.strike = strike
        self.right = right


def test_ibkr_option_chain_source_returns_dataframe() -> None:
    ib = DummyOptionIB()

    def underlying_factory(symbol: str) -> SimpleNamespace:
        return SimpleNamespace(symbol=symbol, secType="STK", conId=10)

    source = IBKROptionChainSource(
        ib=ib,
        underlying_factory=underlying_factory,
        option_factory=lambda sym, exp, strike, right: SimpleOption(sym, exp, strike, right),
        max_contracts_per_side=2,
        max_snapshots_per_session=5,
        min_request_interval_seconds=0.0,
    )

    request = OptionChainRequest(symbol="AAPL", expiry=datetime(2024, 1, 19, tzinfo=UTC))
    chain = source.get_option_chain(request)

    assert not chain.calls.empty
    assert not chain.puts.empty
    assert set(chain.calls["right"]) == {"C"}
    assert set(chain.puts["right"]) == {"P"}
    assert "strike" in chain.calls.columns
    assert "bid" in chain.puts.columns
