"""Option chain request/response abstractions and caching utilities."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

import pandas as pd

from .constants import (
    CACHE_STALENESS_WARNING_FRACTION,
    DEFAULT_CACHE_TTL_SECONDS,
    OPTION_CHAIN_METADATA_FILENAME,
    OPTION_CHAIN_SCHEMA_VERSION,
)
from .utils import file_lock, write_csv_atomic, write_json_atomic

logger = logging.getLogger(__name__)


def _ensure_utc(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


@dataclass(slots=True)
class OptionChainRequest:
    """Describes an option chain snapshot request."""

    symbol: str
    expiry: datetime

    def __post_init__(self) -> None:
        self.symbol = self.symbol.upper().strip()
        self.expiry = _ensure_utc(self.expiry)

    @property
    def expiry_label(self) -> str:
        return self.expiry.strftime("%Y%m%d")


@dataclass(slots=True)
class OptionChain:
    """Container for call and put option tables."""

    calls: pd.DataFrame
    puts: pd.DataFrame


class OptionChainSource(Protocol):
    """Protocol implemented by data providers capable of returning option chains."""

    def get_option_chain(self, request: OptionChainRequest) -> OptionChain:
        ...


class OptionChainCacheStore:
    """Filesystem backed cache for option chains."""

    def __init__(
        self,
        base_dir: Path,
        *,
        max_age_seconds: float | None = DEFAULT_CACHE_TTL_SECONDS,
        warning_handler: Callable[[str, dict[str, object] | None], None] | None = None,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._max_age_seconds = max_age_seconds
        self._warning_handler = warning_handler

    def load_option_chain(self, request: OptionChainRequest) -> OptionChain | None:
        calls_path, puts_path, metadata_path = self._paths_for_request(request)
        if not calls_path.exists() or not puts_path.exists() or not metadata_path.exists():
            return None
        if self._is_expired(metadata_path):
            logger.debug(
                "Option chain cache expired for %s expiry=%s",
                request.symbol,
                request.expiry_label,
            )
            return None
        calls = pd.read_csv(calls_path)
        puts = pd.read_csv(puts_path)
        self._warn_if_stale(metadata_path)
        logger.debug(
            "Loaded cached option chain for %s expiry=%s", request.symbol, request.expiry_label
        )
        return OptionChain(calls=calls, puts=puts)

    def store_option_chain(self, request: OptionChainRequest, chain: OptionChain) -> tuple[Path, Path]:
        calls_path, puts_path, metadata_path = self._paths_for_request(request)
        calls_path.parent.mkdir(parents=True, exist_ok=True)

        with file_lock(calls_path):
            write_csv_atomic(calls_path, chain.calls, index=False)
            write_csv_atomic(puts_path, chain.puts, index=False)
            write_json_atomic(
                metadata_path,
                {
                    "symbol": request.symbol,
                    "expiry": request.expiry_label,
                    "stored_at": time.time(),
                    "schema_version": OPTION_CHAIN_SCHEMA_VERSION,
                },
            )

        logger.debug(
            "Stored option chain for %s expiry=%s (calls=%d puts=%d)",
            request.symbol,
            request.expiry_label,
            len(chain.calls),
            len(chain.puts),
        )
        return calls_path, puts_path

    def _paths_for_request(self, request: OptionChainRequest) -> tuple[Path, Path, Path]:
        symbol = request.symbol.lower()
        digest = hashlib.sha256(request.expiry_label.encode("utf-8")).hexdigest()[:12]
        directory = self._base_dir / symbol / request.expiry_label
        calls_path = directory / f"calls_{digest}.csv"
        puts_path = directory / f"puts_{digest}.csv"
        metadata_path = directory / OPTION_CHAIN_METADATA_FILENAME
        return calls_path, puts_path, metadata_path

    def _is_expired(self, metadata_path: Path) -> bool:
        if self._max_age_seconds is None:
            return False
        try:
            with metadata_path.open("r", encoding="utf-8") as handle:
                metadata = json.load(handle)
            stored_at = float(metadata.get("stored_at", 0.0))
            schema_version = metadata.get("schema_version")
        except (FileNotFoundError, json.JSONDecodeError, ValueError):  # pragma: no cover
            return True
        if schema_version != OPTION_CHAIN_SCHEMA_VERSION:
            logger.debug(
                "Metadata schema mismatch for %s (found=%s, expected=%s)",
                metadata_path,
                schema_version,
                OPTION_CHAIN_SCHEMA_VERSION,
            )
            return True
        expired = (time.time() - stored_at) > self._max_age_seconds
        if expired:
            logger.debug(
                "Option chain metadata %s is older than %.2fs",
                metadata_path,
                self._max_age_seconds,
            )
        return expired

    def metadata_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for metadata_path in self._base_dir.glob(f"**/{OPTION_CHAIN_METADATA_FILENAME}"):
            try:
                with metadata_path.open("r", encoding="utf-8") as handle:
                    metadata = json.load(handle)
            except (FileNotFoundError, json.JSONDecodeError):  # pragma: no cover
                continue
            symbol = metadata.get("symbol") or metadata_path.parent.parent.name.upper()
            expiry = metadata.get("expiry") or metadata_path.parent.name
            stored_at = metadata.get("stored_at")
            age = None
            if isinstance(stored_at, (int, float)):
                age = max(0.0, time.time() - float(stored_at))
            entries.append(
                {
                    "symbol": symbol,
                    "expiry": expiry,
                    "schema_version": metadata.get("schema_version"),
                    "age_seconds": age,
                    "path": metadata_path,
                }
            )
        entries.sort(key=lambda item: (item.get("symbol") or "", item.get("expiry") or ""))
        return entries

    @property
    def max_age_seconds(self) -> float | None:
        return self._max_age_seconds

    def _warn_if_stale(self, metadata_path: Path) -> None:
        if self._max_age_seconds is None:
            return
        age = self._age_seconds(metadata_path)
        if age is None:
            return
        if age >= self._max_age_seconds * CACHE_STALENESS_WARNING_FRACTION:
            logger.warning(
                "Option chain cache at %s nearing TTL (age=%.0fs ttl=%.0fs)",
                metadata_path.parent,
                age,
                self._max_age_seconds,
            )
            if self._warning_handler is not None:
                self._warning_handler(
                    "Option chain cache entry nearing TTL",
                    {
                        "path": str(metadata_path.parent),
                        "age_seconds": age,
                        "ttl_seconds": self._max_age_seconds,
                    },
                )

    @staticmethod
    def _age_seconds(metadata_path: Path) -> float | None:
        try:
            with metadata_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError):  # pragma: no cover
            return None
        stored_at = payload.get("stored_at")
        if not isinstance(stored_at, (int, float)):
            return None
        return max(0.0, time.time() - float(stored_at))


class OptionChainClient:
    """Fetch option chains with optional caching."""

    def __init__(
        self,
        *,
        source: OptionChainSource,
        cache: OptionChainCacheStore | None = None,
    ) -> None:
        self._source = source
        self._cache = cache

    def get_option_chain(self, request: OptionChainRequest) -> OptionChain:
        cached = self._cache.load_option_chain(request) if self._cache is not None else None
        if cached is not None:
            logger.debug(
                "Returning cached option chain for %s expiry=%s",
                request.symbol,
                request.expiry_label,
            )
            return OptionChain(calls=cached.calls.copy(), puts=cached.puts.copy())

        chain = self._source.get_option_chain(request)
        if self._cache is not None:
            self._cache.store_option_chain(request, chain)
        logger.debug(
            "Fetched option chain via source for %s expiry=%s",
            request.symbol,
            request.expiry_label,
        )
        return OptionChain(calls=chain.calls.copy(), puts=chain.puts.copy())
