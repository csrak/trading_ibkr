"""Market data helpers backed by ib_insync / IBKR."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Iterable, Sequence

import pandas as pd

from .constants import (
    IBKR_BAR_SIZE_MAP,
    IBKR_DEFAULT_CURRENCY,
    IBKR_DEFAULT_EXCHANGE,
    IBKR_HISTORICAL_DATA_USE_RTH,
    IBKR_HISTORICAL_DATA_WHAT_TO_SHOW,
    IBKR_HISTORICAL_DATE_FORMAT,
)
from .market_data import PriceBarRequest
from .models import OptionRight
from .options import OptionChain, OptionChainRequest

try:  # pragma: no cover - always available in production envs
    from ib_insync import IB, Contract, Option, Stock  # type: ignore
except Exception:  # pragma: no cover - allows tests to inject dummies
    IB = object  # type: ignore[misc,assignment]
    Contract = object  # type: ignore[misc,assignment]
    Option = object  # type: ignore[misc,assignment]
    Stock = object  # type: ignore[misc,assignment]


logger = logging.getLogger(__name__)


class IBKRRequestError(RuntimeError):
    """Base exception for IBKR data requests."""

    def __init__(self, message: str, *, symbol: str | None = None, context: dict | None = None) -> None:
        super().__init__(message)
        self.symbol = symbol
        self.context = context or {}


class IBKRConnectionError(IBKRRequestError):
    """Raised when the IB client connection is not active."""


class IBKRThrottleError(IBKRRequestError):
    """Raised when IBKR throttling limits are exceeded."""

    def __init__(self, message: str, *, symbol: str | None, limit: int, used: int, context: dict | None = None) -> None:
        super().__init__(message, symbol=symbol, context=context)
        self.limit = limit
        self.used = used


class SnapshotLimitError(IBKRThrottleError):
    """Raised when the configured snapshot quota is exceeded."""


def _ensure_connected(ib: IB, *, symbol: str | None = None) -> None:
    if hasattr(ib, "isConnected") and not ib.isConnected():  # type: ignore[attr-defined]
        raise IBKRConnectionError("IBKR client is not connected", symbol=symbol)


def _duration_string(request: PriceBarRequest) -> str:
    delta = request.end - request.start
    seconds = max(delta.total_seconds(), 1)
    days = max(int(math.ceil(seconds / 86400)), 1)
    return f"{days} D"


def _bar_size(interval: str) -> str:
    return IBKR_BAR_SIZE_MAP.get(interval, IBKR_BAR_SIZE_MAP["1d"])


def _default_contract_factory(symbol: str) -> Contract:
    if Contract is object:  # pragma: no cover - tests provide their own
        return {"symbol": symbol}
    return Stock(symbol, IBKR_DEFAULT_EXCHANGE, IBKR_DEFAULT_CURRENCY)


def _default_option_factory(symbol: str, expiry: str, strike: float, right: str) -> Option:
    if Option is object:  # pragma: no cover - tests provide their own
        return type("SimpleOption", (), {"symbol": symbol, "lastTradeDateOrContractMonth": expiry, "strike": strike, "right": right})()
    return Option(symbol, expiry, strike, right, IBKR_DEFAULT_EXCHANGE)


def _default_underlying_factory(symbol: str) -> Contract:
    if Stock is object:  # pragma: no cover - tests provide their own
        return type("Underlying", (), {"symbol": symbol, "secType": "STK", "conId": 0})()
    return Stock(symbol, IBKR_DEFAULT_EXCHANGE, IBKR_DEFAULT_CURRENCY)


class _RateLimiter:
    def __init__(self, *, max_calls: int, min_interval: float) -> None:
        self._max_calls = max_calls
        self._min_interval = min_interval
        self._calls = 0
        self._last_request: float | None = None

    def track(self, *, symbol: str | None = None, context: str | None = None) -> None:
        if self._calls >= self._max_calls:
            raise SnapshotLimitError(
                "Snapshot limit exceeded",
                symbol=symbol,
                limit=self._max_calls,
                used=self._calls,
                context={"context": context} if context else None,
            )
        now = time.monotonic()
        if self._last_request is not None:
            delta = now - self._last_request
            if delta < self._min_interval:
                sleep_for = self._min_interval - delta
                logger.debug("Rate limiter sleeping for %.2fs (%s)", sleep_for, context)
                time.sleep(sleep_for)
        self._last_request = time.monotonic()
        self._calls += 1
        logger.debug(
            "Rate limiter call #%d/%d (%s)",
            self._calls,
            self._max_calls,
            context,
        )

    @property
    def calls_used(self) -> int:
        return self._calls

    @property
    def call_limit(self) -> int:
        return self._max_calls

    def reset(self) -> None:
        self._calls = 0
        self._last_request = None


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
        _ensure_connected(self._ib, symbol=request.symbol)
        self._limiter.track(symbol=request.symbol, context=f"historical:{request.interval}")

        contract = self._contract_factory(request.symbol)
        duration = _duration_string(request)
        bar_size = _bar_size(request.interval)
        symbol = getattr(contract, "symbol", request.symbol)

        logger.debug(
            "Requesting IBKR historical data symbol=%s duration=%s bar_size=%s",
            symbol,
            duration,
            bar_size,
        )

        try:
            bars = self._ib.reqHistoricalData(
                contract,
                endDateTime=request.end.strftime(IBKR_HISTORICAL_DATE_FORMAT),
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=IBKR_HISTORICAL_DATA_WHAT_TO_SHOW,
                useRTH=IBKR_HISTORICAL_DATA_USE_RTH,
                formatDate=1,
            )
        except SnapshotLimitError:
            raise
        except Exception as exc:  # pragma: no cover - network/IB dependent
            logger.exception(
                "IBKR historical data request failed symbol=%s duration=%s bar_size=%s",
                symbol,
                duration,
                bar_size,
            )
            raise IBKRRequestError(
                "Failed to retrieve historical data from IBKR",
                symbol=symbol,
                context={"duration": duration, "bar_size": bar_size},
            ) from exc

        if not bars:
            logger.debug("IBKR returned no bars for symbol=%s", symbol)
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
        logger.debug(
            "Received %d bars for symbol=%s (range=%s->%s)",
            len(frame),
            symbol,
            frame.index.min(),
            frame.index.max(),
        )
        return frame

    @property
    def rate_limit_usage(self) -> tuple[int, int]:
        """Return tuple of (used, limit) for the rate limiter."""

        return self._limiter.calls_used, self._limiter.call_limit

    def reset_rate_limiter(self) -> None:
        self._limiter.reset()


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
        _ensure_connected(self._ib, symbol=request.symbol)
        expiry = request.expiry.strftime("%Y%m%d")
        self._limiter.track(symbol=request.symbol, context=f"option_chain:{expiry}")

        underlying = self._underlying_factory(request.symbol)

        logger.debug(
            "Requesting IBKR option chain symbol=%s expiry=%s max_contracts_per_side=%d",
            request.symbol,
            expiry,
            self._max_contracts,
        )

        try:
            params_list = self._ib.reqSecDefOptParams(
                getattr(underlying, "symbol", request.symbol),
                "",
                getattr(underlying, "secType", "STK"),
                getattr(underlying, "conId", 0),
            )
        except SnapshotLimitError:
            raise
        except Exception as exc:  # pragma: no cover - depends on IB responses
            logger.exception(
                "Failed to request option parameters for %s expiry=%s", request.symbol, expiry
            )
            raise IBKRRequestError(
                "Failed to request option parameters from IBKR",
                symbol=request.symbol,
                context={"expiry": expiry},
            ) from exc

        if not params_list:
            raise RuntimeError(f"IBKR returned no option parameters for {request.symbol}")

        params = params_list[0]
        strikes = sorted(float(strike) for strike in getattr(params, "strikes", []))
        if not strikes:
            raise RuntimeError(f"IBKR returned no strikes for symbol {request.symbol}")

        selected = strikes[: self._max_contracts]

        contracts: list[Contract] = []
        for strike in selected:
            contracts.append(self._option_factory(request.symbol, expiry, strike, "C"))
            contracts.append(self._option_factory(request.symbol, expiry, strike, "P"))

        if contracts:
            try:
                self._ib.qualifyContracts(*contracts)
            except Exception as exc:  # pragma: no cover
                logger.exception("qualifyContracts failed for %s", request.symbol)
                raise IBKRRequestError(
                    "Failed to qualify option contracts",
                    symbol=request.symbol,
                    context={"expiry": expiry},
                ) from exc

        try:
            tickers = self._ib.reqTickers(*contracts)
        except Exception as exc:  # pragma: no cover
            logger.exception("reqTickers failed for %s expiry=%s", request.symbol, expiry)
            raise IBKRRequestError(
                "Failed to request option tickers",
                symbol=request.symbol,
                context={"expiry": expiry},
            ) from exc
        ticker_map = list(zip(contracts, tickers))

        call_records = []
        put_records = []
        for contract, ticker in ticker_map:
            right = getattr(contract, "right", OptionRight.CALL.value)
            strike = float(getattr(contract, "strike", 0.0))
            ticker_info = _OptionTicker.from_object(ticker)
            try:
                right_enum = OptionRight(right.upper())
            except ValueError:
                right_enum = OptionRight.CALL
            record = {
                "symbol": request.symbol,
                "expiry": expiry,
                "strike": strike,
                "right": right_enum.value,
                "bid": ticker_info.bid,
                "ask": ticker_info.ask,
                "last": ticker_info.last,
                "mid": ticker_info.mid(),
            }
            if right_enum is OptionRight.CALL:
                call_records.append(record)
            else:
                put_records.append(record)

        calls_frame = pd.DataFrame(call_records)
        puts_frame = pd.DataFrame(put_records)
        logger.debug(
            "Received option chain for %s expiry=%s (calls=%d puts=%d)",
            request.symbol,
            expiry,
            len(calls_frame),
            len(puts_frame),
        )
        return OptionChain(calls=calls_frame, puts=puts_frame)
