"""Utility helpers for safe filesystem operations within ``model.data``."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd

from .constants import LOCK_SUFFIX, TEMP_SUFFIX


@contextmanager
def file_lock(path: Path, *, timeout: float = 5.0, poll_interval: float = 0.1) -> Iterator[None]:
    """Acquire a lightweight filesystem lock for ``path``.

    The implementation relies on an exclusive lock file.  It is intentionally
    simple but sufficient for preventing concurrent writers within the same
    host.
    """

    lock_path = path.with_suffix(path.suffix + LOCK_SUFFIX)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout

    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timeout waiting for lock on {path}")  # pragma: no cover
            time.sleep(poll_interval)

    try:
        yield
    finally:
        try:
            os.remove(lock_path)
        except FileNotFoundError:  # pragma: no cover - defensive
            pass


def write_csv_atomic(path: Path, frame: pd.DataFrame, *, index: bool = False) -> None:
    """Write a dataframe to ``path`` using a temporary file + rename."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + TEMP_SUFFIX)
    frame.to_csv(temp_path, index=index)
    temp_path.replace(path)


def write_json_atomic(path: Path, payload: dict) -> None:
    """Persist JSON payload to ``path`` atomically."""

    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + TEMP_SUFFIX)
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    temp_path.replace(path)
