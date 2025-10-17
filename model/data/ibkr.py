"""Market data helpers backed by ib_insync / IBKR."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Callable, Iterable, Sequence

import pandas as pd

from .market_data import PriceBarRequest
from .options import OptionChain, OptionChainRequest

try:  # pragma: no cover - always available in production envs
    from ib_insync import IB, Contract, Option, Stock  # type: ignore
except Exception:  # pragma: no cover - allows tests to inject dummies
    IB = object  # type: ignore[misc,assignment]
    Contract = object  # type: ignore[misc,assignment]
    Option = object  # type: ignore[misc,assignment]
    Stock = object  # type: ignore[misc,assignment]


class SnapshotLimitError(RuntimeError):
    """Raised when the configured snapshot quota is exceeded."""


def _ensure_connected(ib: IB) -> None:
    if hasattr(ib, "isConnected") and not ib.isConnected():  # type: ignore[attr-defined]
        raise RuntimeError("IB client is not connected")


def _duration_string(request: PriceBarRequest) -> str:
    delta = request.end - request.start
    seconds = max(delta.total_seconds(), 1)
    days = max(int(math.ceil(seconds / 86400)), 1)
    return f"{days} D"


def _bar_size(interval: str) -> str:
    mapping = {
        "1d": "1 day",
        "1h": "1 hour",
        "30m": "30 mins",
        "15m": "15 mins",
        "5m": "5 mins",
        "1m": "1 min",
    }
    return mapping.get(interval, "1 day")


def _default_contract_factory(symbol: str) -> Contract:
    if Contract is object:  # pragma: no cover - tests provide their own
        return {"symbol": symbol}
    return Stock(symbol, "SMART", "USD")


def _default_option_factory(symbol: str, expiry: str, strike: float, right: str) -> Option:
    if Option is object:  # pragma: no cover - tests provide their own
        return type("SimpleOption", (), {"symbol": symbol, "lastTradeDateOrContractMonth": expiry, "strike": strike, "right": right})()
    return Option(symbol, expiry, strike, right, "SMART")


def _default_underlying_factory(symbol: str) -> Contract:
    if Stock is object:  # pragma: no cover - tests provide their own
        return type("Underlying", (), {"symbol": symbol, "secType": "STK", "conId": 0})()
    return Stock(symbol, "SMART", "USD")


class _RateLimiter:
    def __init__(self, *, max_calls: int, min_interval: float) -> None:
        self._max_calls = max_calls
        self._min_interval = min_interval
        self._calls = 0
        self._last_request: float | None = None

    def track(self) -> None:
        if self._calls >= self._max_calls:
            raise SnapshotLimitError(
                f"Snapshot limit exceeded ({self._calls}/{self._max_calls} requests used)"
            )
        now = time.monotonic()
        if self._last_request is not None:
            delta = now - self._last_request
            if delta < self._min_interval:
                time.sleep(self._min_interval - delta)
        self._last_request = time.monotonic()
        self._calls += 1


class IBKRMarketDataSource:
    """Historical price bars retrieved via IBKR."""

    def __init__(
        self,
        *,
        ib: IB | None = None,
        contract_factory: Callable[[str], Contract] = _default_contract_factory,
        max_snapshots_per_session: int = 60,
        min_request_interval_seconds: float = 1.0,
    ) -> None:
        self._ib = ib or IB()
        self._contract_factory = contract_factory
        self._limiter = _RateLimiter(
            max_calls=max_snapshots_per_session,
            min_interval=min_request_interval_seconds,
        )

    def get_price_bars(self, request: PriceBarRequest) -> pd.DataFrame:
        _ensure_connected(self._ib)
        self._limiter.track()

        contract = self._contract_factory(request.symbol)
        duration = _duration_string(request)
        bar_size = _bar_size(request.interval)

        bars = self._ib.reqHistoricalData(
            contract,
            endDateTime=request.end.strftime("%Y%m%d %H:%M:%S"),
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=0,
            formatDate=1,
        )

        if not bars:
            return pd.DataFrame(
                columns=["open", "high", "low", "close", "volume", "average", "bar_count"]
            )

        records = []
        for bar in bars:
            timestamp = getattr(bar, "date", None)
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp)
            if timestamp is None:
                continue
            timestamp = timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)
            records.append(
                {
                    "timestamp": timestamp,
                    "open": float(getattr(bar, "open", 0.0)),
                    "high": float(getattr(bar, "high", 0.0)),
                    "low": float(getattr(bar, "low", 0.0)),
                    "close": float(getattr(bar, "close", 0.0)),
                    "volume": float(getattr(bar, "volume", 0.0)),
                    "average": float(getattr(bar, "average", 0.0)),
                    "bar_count": int(getattr(bar, "barCount", getattr(bar, "bar_count", 0))),
                }
            )

        frame = pd.DataFrame.from_records(records).set_index("timestamp").sort_index()
        return frame


@dataclass(slots=True)
class _OptionTicker:
    bid: float | None
    ask: float | None
    last: float | None

    @classmethod
    def from_object(cls, obj: object) -> "_OptionTicker":
        bid = getattr(obj, "bid", None)
        ask = getattr(obj, "ask", None)
        last_attr = getattr(obj, "last", None)
        if last_attr is None and hasattr(obj, "marketPrice"):
            mp = getattr(obj, "marketPrice")
            last_attr = mp() if callable(mp) else mp
        return cls(
            bid=float(bid) if bid is not None else None,
            ask=float(ask) if ask is not None else None,
            last=float(last_attr) if last_attr is not None else None,
        )

    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2


class IBKROptionChainSource:
    """Option chain snapshots retrieved via IBKR."""

    def __init__(
        self,
        *,
        ib: IB | None = None,
        underlying_factory: Callable[[str], Contract] = _default_underlying_factory,
        option_factory: Callable[[str, str, float, str], Contract] = _default_option_factory,
        max_contracts_per_side: int = 20,
        max_snapshots_per_session: int = 100,
        min_request_interval_seconds: float = 1.0,
    ) -> None:
        self._ib = ib or IB()
        self._underlying_factory = underlying_factory
        self._option_factory = option_factory
        self._max_contracts = max_contracts_per_side
        self._limiter = _RateLimiter(
            max_calls=max_snapshots_per_session,
            min_interval=min_request_interval_seconds,
        )

    def get_option_chain(self, request: OptionChainRequest) -> OptionChain:
        _ensure_connected(self._ib)
        self._limiter.track()

        expiry = request.expiry.strftime("%Y%m%d")
        underlying = self._underlying_factory(request.symbol)

        params_list = self._ib.reqSecDefOptParams(
            getattr(underlying, "symbol", request.symbol),
            "",
            getattr(underlying, "secType", "STK"),
            getattr(underlying, "conId", 0),
        )

        if not params_list:
            raise RuntimeError("IBKR returned no option parameters")

        params = params_list[0]
        strikes = sorted(float(strike) for strike in getattr(params, "strikes", []))
        if not strikes:
            raise RuntimeError("IBKR returned no strikes for option chain")

        selected = strikes[: self._max_contracts]

        contracts: list[Contract] = []
        for strike in selected:
            contracts.append(self._option_factory(request.symbol, expiry, strike, "C"))
            contracts.append(self._option_factory(request.symbol, expiry, strike, "P"))

        if contracts:
            self._ib.qualifyContracts(*contracts)

        tickers = self._ib.reqTickers(*contracts)
        ticker_map = list(zip(contracts, tickers))

        call_records = []
        put_records = []
        for contract, ticker in ticker_map:
            right = getattr(contract, "right", "C")
            strike = float(getattr(contract, "strike", 0.0))
            ticker_info = _OptionTicker.from_object(ticker)
            record = {
                "symbol": request.symbol,
                "expiry": expiry,
                "strike": strike,
                "right": right,
                "bid": ticker_info.bid,
                "ask": ticker_info.ask,
                "last": ticker_info.last,
                "mid": ticker_info.mid(),
            }
            if right.upper() == "C":
                call_records.append(record)
            else:
                put_records.append(record)

        calls_frame = pd.DataFrame(call_records)
        puts_frame = pd.DataFrame(put_records)
        return OptionChain(calls=calls_frame, puts=puts_frame)
