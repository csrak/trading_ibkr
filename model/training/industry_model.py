"""Example pipeline for training a simple industry-based price model.

The goal is to demonstrate how to structure model development separately from
runtime execution. The model is intentionally naive: it fits a linear
relationship between the target stock's future price and the daily returns of
peer symbols in the same industry. The trained coefficients and predictions are
stored on disk for reuse in backtests or live inference.

Usage (offline):

    from model.training.industry_model import train_linear_industry_model

    train_linear_industry_model(
        target_symbol="AAPL",
        peer_symbols=["MSFT", "GOOGL", "AMZN"],
        start="2023-01-01",
        end="2024-01-01",
        horizon_days=5,
        artifact_dir="model/artifacts/industry_forecast",
    )

This script depends on `yfinance` for data retrieval. Install the optional
training extras:

    uv pip install -e ".[training]"
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf

from model.data.client import MarketDataClient
from model.data.market_data import PriceBarRequest


@dataclass(slots=True)
class LinearIndustryModel:
    """Serialized representation of the trained linear model."""

    target: str
    peers: list[str]
    horizon_days: int
    intercept: float
    coefficients: dict[str, float]
    train_start: str
    train_end: str
    created_at: str
    prediction_path: str

    def to_json(self) -> dict[str, object]:
        return {
            "target": self.target,
            "peers": self.peers,
            "horizon_days": self.horizon_days,
            "intercept": self.intercept,
            "coefficients": self.coefficients,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "created_at": self.created_at,
            "prediction_path": self.prediction_path,
        }


def _download_prices(
    symbols: Iterable[str],
    start: str,
    end: str,
    data_client: MarketDataClient | None = None,
) -> pd.DataFrame:
    if data_client is None:
        data = yf.download(list(symbols), start=start, end=end, auto_adjust=False, progress=False)
    else:
        frames: list[pd.Series] = []
        start_dt = datetime.fromisoformat(start).replace(tzinfo=UTC)
        end_dt = datetime.fromisoformat(end).replace(tzinfo=UTC)
        for symbol in symbols:
            request = PriceBarRequest(
                symbol=symbol,
                start=start_dt,
                end=end_dt,
                interval="1d",
                auto_adjust=False,
            )
            frame = data_client.get_price_bars(request)
            candidate_col = next((name for name in ("adj_close", "close") if name in frame.columns), None)
            if candidate_col is None:
                available = frame.columns.tolist()
                raise KeyError(f"Expected 'adj_close' or 'close' columns; got {available}")
            series = frame[candidate_col].rename(symbol)
            frames.append(series)
        data = pd.concat(frames, axis=1)

    if data.empty:
        raise ValueError("No price data returned from yfinance. Check symbols or date range.")

    if data_client is not None:
        standardized = data
    elif isinstance(data.columns, pd.MultiIndex):
        price_level = None
        level_index = None
        for candidate in ("Adj Close", "Close"):
            for idx in range(data.columns.nlevels):
                if candidate in data.columns.get_level_values(idx):
                    price_level = candidate
                    level_index = idx
                    break
            if price_level is not None:
                break
        if price_level is None or level_index is None:
            available = [data.columns.get_level_values(i).unique().tolist() for i in range(data.columns.nlevels)]
            raise KeyError(f"Expected 'Adj Close' or 'Close' columns, got levels: {available}")
        data = data.xs(price_level, level=level_index, axis=1)
        data.columns = [col.upper() for col in data.columns]
        standardized = data
    else:
        if "Adj Close" in data.columns:
            data = data[["Adj Close"]]
        elif "Close" in data.columns:
            data = data[["Close"]]
        else:
            raise KeyError(f"Expected 'Adj Close' or 'Close' in columns; got {data.columns.tolist()}")
        if len(symbols) == 1:
            data.columns = [symbols[0]]
        else:
            raise ValueError("Single-column dataset returned for multiple symbols")
        standardized = data

    standardized = standardized.dropna(how="all")
    missing = [symbol for symbol in symbols if symbol not in standardized.columns]
    if missing:
        raise ValueError(f"Missing price columns for symbols: {missing}")
    return standardized[symbols]


def _prepare_features(
    prices: pd.DataFrame,
    target: str,
    peers: list[str],
    horizon_days: int,
) -> tuple[pd.DataFrame, pd.Series]:
    returns = prices.pct_change().dropna()

    feature_frame = returns[[target] + peers].rename(columns={target: "target_return"})
    future_price = prices[target].shift(-horizon_days)

    dataset = feature_frame.join(future_price.rename("future_price")).dropna()

    features = dataset[peers]
    target_prices = dataset["future_price"]
    return features, target_prices


def _fit_linear_regression(features: pd.DataFrame, targets: pd.Series) -> tuple[float, np.ndarray]:
    X = features.to_numpy()
    ones = np.ones((X.shape[0], 1))
    X_aug = np.hstack([ones, X])
    y = targets.to_numpy()

    coeffs, *_ = np.linalg.lstsq(X_aug, y, rcond=None)
    intercept = float(coeffs[0])
    peer_coeffs = coeffs[1:]
    return intercept, peer_coeffs


def _make_predictions(
    intercept: float,
    coeffs: np.ndarray,
    features: pd.DataFrame,
) -> pd.Series:
    X = features.to_numpy()
    preds = intercept + X @ coeffs
    return pd.Series(preds, index=features.index, name="predicted_price")


def _save_visualizations(
    artifact_dir: Path,
    target_symbol: str,
    predictions: pd.Series,
    actual_future_prices: pd.Series,
    coefficients: dict[str, float],
) -> None:
    """Persist simple figures summarizing training outcomes."""

    artifact_dir.mkdir(parents=True, exist_ok=True)

    comparison = pd.DataFrame(
        {
            "Predicted": predictions,
            "Actual": actual_future_prices.reindex(predictions.index),
        }
    ).dropna()

    if not comparison.empty:
        fig, ax = plt.subplots(figsize=(10, 4))
        comparison.plot(ax=ax)
        ax.set_title(f"{target_symbol} predicted vs actual future price")
        ax.set_xlabel("Date")
        ax.set_ylabel("Price (USD)")
        ax.legend(loc="best")
        fig.autofmt_xdate()
        fig.savefig(artifact_dir / f"{target_symbol}_predicted_vs_actual.png", bbox_inches="tight")
        plt.close(fig)

    if coefficients:
        fig, ax = plt.subplots(figsize=(8, 4))
        coeff_items = list(coefficients.items())
        symbols, values = zip(*coeff_items)
        ax.bar(symbols, values, color="#1f77b4")
        ax.set_title(f"{target_symbol} peer coefficients")
        ax.set_ylabel("Weight")
        ax.set_xlabel("Peer Symbol")
        ax.axhline(0, color="black", linewidth=0.8)
        fig.savefig(artifact_dir / f"{target_symbol}_peer_coefficients.png", bbox_inches="tight")
        plt.close(fig)


def train_linear_industry_model(
    target_symbol: str,
    peer_symbols: Iterable[str],
    start: str,
    end: str,
    horizon_days: int,
    artifact_dir: str | Path,
    data_client: MarketDataClient | None = None,
) -> Path:
    """Train the example model and persist artifacts.

    Args:
        target_symbol: Ticker to forecast.
        peer_symbols: Peer tickers from the same industry.
        start: Start date for historical window (YYYY-MM-DD).
        end: End date for historical window.
        horizon_days: Forecast horizon in trading days.
        artifact_dir: Directory where model/json/predictions are saved.
        data_client: Optional market data client used to retrieve prices. Falls back to direct
            yfinance downloads when omitted.

    Returns:
        Path to the persisted model artifact JSON.
    """

    peers = list(peer_symbols)
    if target_symbol in peers:
        raise ValueError("peer_symbols should not contain the target symbol")

    symbols = [target_symbol] + peers
    prices = _download_prices(symbols, start=start, end=end, data_client=data_client)
    if prices.empty:
        raise ValueError("Downloaded price data is empty. Check symbols/start/end range.")

    features, targets = _prepare_features(prices, target_symbol, peers, horizon_days)
    if features.empty:
        raise ValueError("Not enough data points to build training features")

    intercept, coeffs = _fit_linear_regression(features, targets)
    predictions = _make_predictions(intercept, coeffs, features)

    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    model_path = artifact_dir / f"{target_symbol}_linear_model.json"
    predictions_path = artifact_dir / f"{target_symbol}_predictions.csv"

    prediction_index = predictions.index
    if getattr(prediction_index, "tz", None) is None:
        timestamps = prediction_index.tz_localize("UTC")
    else:
        timestamps = prediction_index.tz_convert("UTC")

    pd.DataFrame(
        {
            "timestamp": timestamps.strftime("%Y-%m-%d"),
            "predicted_price": predictions.values,
        }
    ).to_csv(predictions_path, index=False)

    coeff_dict = dict(zip(peers, map(float, coeffs)))
    _save_visualizations(
        artifact_dir=artifact_dir,
        target_symbol=target_symbol,
        predictions=predictions,
        actual_future_prices=targets,
        coefficients=coeff_dict,
    )
    model = LinearIndustryModel(
        target=target_symbol,
        peers=peers,
        horizon_days=horizon_days,
        intercept=float(intercept),
        coefficients=coeff_dict,
        train_start=start,
        train_end=end,
        created_at=datetime.now(UTC).isoformat(),
        prediction_path=predictions_path.name,
    )

    import json

    with model_path.open("w", encoding="utf-8") as fp:
        json.dump(model.to_json(), fp, indent=2)

    return model_path
