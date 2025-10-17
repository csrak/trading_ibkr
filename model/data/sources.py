"""Concrete data sources backed by public APIs (e.g. yfinance)."""

from __future__ import annotations

from datetime import UTC

import pandas as pd

from .market_data import PriceBarRequest
from .options import OptionChain, OptionChainRequest, OptionChainSource

try:
    import yfinance as yf
except ImportError as exc:  # pragma: no cover - import checked in tests
    raise RuntimeError("yfinance must be installed to use the YFinance sources") from exc


class YFinanceMarketDataSource:
    """Fetch historical bars from Yahoo! Finance."""

    def get_price_bars(self, request: PriceBarRequest) -> pd.DataFrame:
        frame = yf.download(
            tickers=request.symbol,
            start=request.start.strftime("%Y-%m-%d"),
            end=request.end.strftime("%Y-%m-%d"),
            interval=request.interval,
            auto_adjust=request.auto_adjust,
            progress=False,
        )

        if isinstance(frame.columns, pd.MultiIndex):
            new_columns = []
            for level0, level1 in frame.columns:
                field = str(level0).strip().lower().replace(" ", "_")
                symbol_part = str(level1).strip().lower()
                new_columns.append(f"{field}_{symbol_part}")
            frame.columns = new_columns
        else:
            frame = frame.rename(columns=lambda c: str(c).strip().lower().replace(" ", "_"))

        if frame.index.tz is None:
            frame.index = frame.index.tz_localize(UTC)

        return frame


class YFinanceOptionChainSource(OptionChainSource):
    """Fetch option chains via Yahoo! Finance."""

    def get_option_chain(self, request: OptionChainRequest) -> OptionChain:
        ticker = yf.Ticker(request.symbol)
        chain = ticker.option_chain(request.expiry.strftime("%Y-%m-%d"))
        calls = chain.calls.copy()
        puts = chain.puts.copy()
        return OptionChain(calls=calls, puts=puts)
