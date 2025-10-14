"""Slow integration test that exercises the industry model with live yfinance data."""

from __future__ import annotations

from pathlib import Path

import pytest

from model.inference.price_predictor import LinearIndustryArtifact
from model.training.industry_model import train_linear_industry_model


@pytest.mark.slow
def test_train_linear_industry_model_with_live_data(tmp_path: Path) -> None:
    """Train the demo model end-to-end using real market data."""

    try:
        artifact_path = train_linear_industry_model(
            target_symbol="AAPL",
            peer_symbols=["MSFT", "GOOGL"],
            start="2023-01-01",
            end="2024-01-01",
            horizon_days=5,
            artifact_dir=tmp_path,
        )
    except Exception as exc:  # pragma: no cover - network flake handling
        pytest.skip(f"Live data retrieval failed: {exc}")

    artifact = LinearIndustryArtifact.load(artifact_path)
    predictions = artifact.load_predictions(artifact_path)

    assert artifact.target == "AAPL"
    assert artifact.peers == ["MSFT", "GOOGL"]
    assert len(artifact.coefficients) == 2
    assert predictions.notna().all()
