"""Training pipelines for machine learning models.

This module is intentionally isolated from live trading code. It should contain
feature engineering, model training, and serialization logic executed offline.
"""

from __future__ import annotations

# Placeholder: implement feature engineering + model training functions here.

def train_example_model() -> None:  # pragma: no cover - illustrative stub
    """Example training function.

    In production this would load data, engineer features, fit a model, and
    persist the artifact via `model.registry` utilities.
    """

    raise NotImplementedError("Training pipeline not implemented yet")
