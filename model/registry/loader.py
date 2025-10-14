"""Model registry helpers for loading trained artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class ModelLoader(Protocol):
    """Protocol implemented by model loaders.

    Allows callers to retrieve model artifacts without knowing the storage
    backend (filesystem, object store, ML platform, etc.).
    """

    def load(self, artifact_path: Path) -> Any:
        """Load a model artifact from the given path."""


class LocalPickleLoader:
    """Loads pickled model artifacts from the local filesystem."""

    def load(self, artifact_path: Path) -> Any:
        import pickle

        with artifact_path.open("rb") as fp:
            return pickle.load(fp)
