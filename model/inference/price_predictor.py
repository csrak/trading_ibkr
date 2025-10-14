"""Utilities for loading trained industry models and generating predictions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(slots=True)
class LinearIndustryArtifact:
    target: str
    peers: list[str]
    horizon_days: int
    intercept: float
    coefficients: dict[str, float]
    train_start: str
    train_end: str
    created_at: str
    prediction_path: str

    @classmethod
    def load(cls, path: Path) -> "LinearIndustryArtifact":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            target=data["target"],
            peers=data["peers"],
            horizon_days=int(data["horizon_days"]),
            intercept=float(data["intercept"]),
            coefficients={k: float(v) for k, v in data["coefficients"].items()},
            train_start=data["train_start"],
            train_end=data["train_end"],
            created_at=data["created_at"],
            prediction_path=data["prediction_path"],
        )

    def load_predictions(self, artifact_file: Path) -> pd.Series:
        """Load the saved prediction CSV as a Series indexed by date string."""

        prediction_file = artifact_file.parent / self.prediction_path
        frame = pd.read_csv(prediction_file)
        if "timestamp" not in frame.columns or "predicted_price" not in frame.columns:
            raise ValueError("Prediction file must contain 'timestamp' and 'predicted_price' columns")
        series = pd.Series(frame["predicted_price"].values, index=frame["timestamp"], name="predicted_price")
        return series


def predict_price(
    artifact: LinearIndustryArtifact,
    latest_prices: dict[str, float],
    target_symbol: str | None = None,
) -> float:
    """Generate a single-step forecast using the loaded coefficients."""

    target = target_symbol or artifact.target
    features = np.array([latest_prices[symbol] for symbol in artifact.peers], dtype=float)
    coeffs = np.array([artifact.coefficients[symbol] for symbol in artifact.peers], dtype=float)
    return float(artifact.intercept + features @ coeffs)
