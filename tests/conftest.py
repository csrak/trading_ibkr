"""Pytest configuration for shared fixtures."""

from __future__ import annotations

import pytest
from loguru import logger


@pytest.fixture(scope="session", autouse=True)
def silence_loguru_handlers() -> None:
    """Route Loguru output to a no-op sink during tests to avoid closed stream errors."""
    logger.remove()
    logger.add(lambda _: None, catch=True)
    yield
