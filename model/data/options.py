"""Option chain request/response abstractions and caching utilities."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import pandas as pd


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

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)

    def load_option_chain(self, request: OptionChainRequest) -> OptionChain | None:
        calls_path, puts_path = self._paths_for_request(request)
        if not calls_path.exists() or not puts_path.exists():
            return None
        calls = pd.read_csv(calls_path)
        puts = pd.read_csv(puts_path)
        return OptionChain(calls=calls, puts=puts)

    def store_option_chain(self, request: OptionChainRequest, chain: OptionChain) -> tuple[Path, Path]:
        calls_path, puts_path = self._paths_for_request(request)
        calls_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_calls = calls_path.with_suffix(".calls.tmp")
        tmp_puts = puts_path.with_suffix(".puts.tmp")
        chain.calls.to_csv(tmp_calls, index=False)
        chain.puts.to_csv(tmp_puts, index=False)
        tmp_calls.replace(calls_path)
        tmp_puts.replace(puts_path)
        return calls_path, puts_path

    def _paths_for_request(self, request: OptionChainRequest) -> tuple[Path, Path]:
        symbol = request.symbol.lower()
        digest = hashlib.sha256(request.expiry_label.encode("utf-8")).hexdigest()[:12]
        directory = self._base_dir / symbol / request.expiry_label
        calls_path = directory / f"calls_{digest}.csv"
        puts_path = directory / f"puts_{digest}.csv"
        return calls_path, puts_path


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
            return OptionChain(calls=cached.calls.copy(), puts=cached.puts.copy())

        chain = self._source.get_option_chain(request)
        if self._cache is not None:
            self._cache.store_option_chain(request, chain)
        return OptionChain(calls=chain.calls.copy(), puts=chain.puts.copy())
