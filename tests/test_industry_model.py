"""Tests for the sample industry model training and inference helpers."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from model.data.client import MarketDataClient
from model.data.market_data import MarketDataSource, PriceBarRequest

from model.inference.price_predictor import LinearIndustryArtifact, predict_price
from model.training import industry_model


class StubMarketDataSource(MarketDataSource):
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames

    def get_price_bars(self, request: PriceBarRequest) -> pd.DataFrame:
        frame = self.frames[request.symbol]
        return frame.copy()


@pytest.fixture
def sample_prices() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=12, freq="B")
    return pd.DataFrame(
        {
            "AAPL": np.linspace(100, 112, len(dates)),
            "MSFT": np.linspace(200, 208, len(dates)),
            "GOOGL": np.linspace(300, 312, len(dates)),
        },
        index=dates,
    )


@pytest.fixture
def trained_artifact_path(tmp_path: Path, sample_prices: pd.DataFrame) -> Path:
    frames = {
        symbol: pd.DataFrame({"adj_close": sample_prices[symbol]})
        for symbol in ["AAPL", "MSFT", "GOOGL"]
    }
    data_client = MarketDataClient(source=StubMarketDataSource(frames))

    return industry_model.train_linear_industry_model(
        target_symbol="AAPL",
        peer_symbols=["MSFT", "GOOGL"],
        start="2024-01-01",
        end="2024-02-01",
        horizon_days=2,
        artifact_dir=tmp_path,
        data_client=data_client,
    )


def test_train_linear_industry_model_persists_artifacts(trained_artifact_path: Path) -> None:
    assert trained_artifact_path.exists()

    payload = json.loads(trained_artifact_path.read_text(encoding="utf-8"))

    assert payload["target"] == "AAPL"
    assert payload["peers"] == ["MSFT", "GOOGL"]
    assert payload["horizon_days"] == 2

    prediction_file = trained_artifact_path.parent / payload["prediction_path"]
    assert prediction_file.exists()

    predictions = pd.read_csv(prediction_file)
    assert not predictions.empty
    assert predictions.columns.tolist() == ["timestamp", "predicted_price"]

    viz_pred = trained_artifact_path.parent / "AAPL_predicted_vs_actual.png"
    viz_coeff = trained_artifact_path.parent / "AAPL_peer_coefficients.png"
    assert viz_pred.exists()
    assert viz_coeff.exists()


def test_predict_price_uses_loaded_coefficients(trained_artifact_path: Path) -> None:
    artifact = LinearIndustryArtifact.load(trained_artifact_path)
    predictions = artifact.load_predictions(trained_artifact_path)

    assert artifact.target == "AAPL"
    assert artifact.peers == ["MSFT", "GOOGL"]
    assert predictions.name == "predicted_price"
    assert not predictions.empty

    latest_prices = {"MSFT": 210.5, "GOOGL": 315.25}
    expected = artifact.intercept + sum(
        artifact.coefficients[symbol] * latest_prices[symbol] for symbol in artifact.peers
    )

    assert predict_price(artifact, latest_prices) == pytest.approx(expected)
