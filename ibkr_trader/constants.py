"""Common constants shared across the trading platform."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

SUBSCRIPTION_SOFT_LIMIT = 50
MOCK_PRICE_BASE = Decimal("150.0")
MOCK_PRICE_VARIATION_MODULO = 20
MOCK_PRICE_SLEEP_SECONDS = 5
MARKET_DATA_IDLE_SLEEP_SECONDS = 1
DEFAULT_PORTFOLIO_SNAPSHOT = Path("data/portfolio_snapshot.json")
