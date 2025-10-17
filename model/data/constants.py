"""Centralised constants used across the ``model.data`` package."""

from __future__ import annotations

# ---- IBKR defaults -------------------------------------------------------

IBKR_DEFAULT_EXCHANGE = "SMART"
IBKR_DEFAULT_CURRENCY = "USD"
IBKR_HISTORICAL_DATA_WHAT_TO_SHOW = "TRADES"
IBKR_HISTORICAL_DATA_USE_RTH = 0
IBKR_HISTORICAL_DATE_FORMAT = "%Y%m%d %H:%M:%S"

IBKR_BAR_SIZE_MAP: dict[str, str] = {
    "1d": "1 day",
    "1h": "1 hour",
    "30m": "30 mins",
    "15m": "15 mins",
    "5m": "5 mins",
    "1m": "1 min",
}

# ---- Storage defaults ----------------------------------------------------

SCHEMA_VERSION_FIELD = "schema_version"

ORDER_BOOK_SCHEMA_VERSION = "1.0"
TRADE_SCHEMA_VERSION = "1.0"
OPTION_SURFACE_SCHEMA_VERSION = "1.0"

ORDER_BOOK_FILENAME_TEMPLATE = "{date_label}.csv"
TRADE_FILENAME_TEMPLATE = "{date_label}.csv"
OPTION_SURFACE_FILENAME = "surface.csv"

# ---- File/lock handling --------------------------------------------------

LOCK_SUFFIX = ".lock"
TEMP_SUFFIX = ".tmp"

DEFAULT_CACHE_TTL_SECONDS = 3600.0  # 1 hour

OPTION_CHAIN_SCHEMA_VERSION = "1.0"
OPTION_CHAIN_METADATA_FILENAME = "metadata.json"
