"""Configuration models for multi-strategy execution graphs."""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from ibkr_trader.strategy_configs.config import StrategyConfig, load_strategy_config

SlugPattern: ClassVar[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")

StrategyNodeType = str
CapitalPolicyType = Literal["equal_weight", "fixed", "vol_target"]


class StrategyNodeConfig(BaseModel):
    """Represents a single strategy participant within the coordinator graph."""

    id: str = Field(description="Unique identifier for the strategy node")
    type: StrategyNodeType
    symbols: list[str] = Field(default_factory=list, min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    max_position: int | None = Field(
        default=None, description="Hard cap on position size in shares/contracts"
    )
    max_notional: Decimal | None = Field(
        default=None, description="Hard cap on notional exposure in account currency"
    )
    warmup_bars: int = Field(default=0, ge=0, description="Number of bars required before trading")
    config_path: Path | None = Field(
        default=None,
        description="Path to legacy strategy config (required for config_adapter nodes)",
    )

    model_config = {"extra": "forbid"}

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not SlugPattern.match(value):
            raise ValueError(
                "id must be 1-40 chars and contain only letters, numbers, hyphen, or underscore"
            )
        return value

    @field_validator("symbols")
    @classmethod
    def _normalize_symbols(cls, symbols: list[str]) -> list[str]:
        unique = []
        seen: set[str] = set()
        for sym in symbols:
            normalized = sym.upper()
            if normalized not in seen:
                seen.add(normalized)
                unique.append(normalized)
        return unique

    @field_validator("max_position")
    @classmethod
    def _validate_position(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("max_position must be positive when provided")
        return value

    @field_validator("max_notional")
    @classmethod
    def _validate_notional(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("max_notional must be positive when provided")
        return value

    @field_validator("warmup_bars")
    @classmethod
    def _validate_warmup(cls, value: int) -> int:
        if value > 5000:
            raise ValueError("warmup_bars cannot exceed 5000 to prevent excessive memory usage")
        return value

    @model_validator(mode="after")
    def _validate_config_path(self) -> StrategyNodeConfig:
        if self.type == "config_adapter":
            if self.config_path is None:
                raise ValueError("config_adapter nodes require config_path")
            try:
                load_strategy_config(self.config_path)
            except (FileNotFoundError, ValidationError, ValueError) as exc:
                raise ValueError(f"Invalid strategy config at '{self.config_path}': {exc}") from exc
        else:
            if self.config_path is not None:
                raise ValueError("config_path is only valid for config_adapter nodes")
            registry_types = set(StrategyConfig.REGISTRY.keys())
            if self.type not in {"sma"} | registry_types:
                raise ValueError(f"Unsupported strategy node type '{self.type}'")
        return self


class CapitalPolicyConfig(BaseModel):
    """Configuration for the coordinator's capital allocation policy."""

    type: CapitalPolicyType = Field(default="equal_weight")
    weights: dict[str, Decimal] | None = Field(
        default=None, description="Strategy weights for fixed policy"
    )
    target_vol: Decimal | None = Field(
        default=None, description="Portfolio volatility target (vol_target policy)"
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate_policy(self) -> CapitalPolicyConfig:
        if self.type == "equal_weight":
            if self.weights is not None or self.target_vol is not None:
                raise ValueError("equal_weight policy does not accept weights or target_vol")
        elif self.type == "fixed":
            if not self.weights:
                raise ValueError("fixed policy requires non-empty weights mapping")
            negative = [k for k, v in self.weights.items() if v <= 0]
            if negative:
                raise ValueError(f"fixed policy weights must be positive (invalid: {negative})")
            total = sum(self.weights.values())
            if total > Decimal("1.0"):
                raise ValueError("fixed policy weights must sum to <= 1.0")
        elif self.type == "vol_target":
            if self.target_vol is None or self.target_vol <= 0:
                raise ValueError("vol_target policy requires positive target_vol")
        else:  # pragma: no cover - Literal guard
            raise ValueError(f"Unsupported capital policy type '{self.type}'")
        return self


class GraphRuntimeSettings(BaseModel):
    """Runtime tuning parameters for the strategy coordinator."""

    allow_partial_start: bool = Field(
        default=False,
        description="If true, coordinator can start when some strategies fail to initialize",
    )
    heartbeat_timeout_seconds: int = Field(default=30, ge=5, le=600)
    telemetry_interval_seconds: int = Field(default=60, ge=10, le=600)

    model_config = {"extra": "forbid"}


class StrategyGraphConfig(BaseModel):
    """Top-level configuration for the multi-strategy coordinator graph."""

    name: str = Field(default="default_graph")
    strategies: list[StrategyNodeConfig] = Field(min_length=1)
    capital_policy: CapitalPolicyConfig = Field(default_factory=CapitalPolicyConfig)
    settings: GraphRuntimeSettings = Field(default_factory=GraphRuntimeSettings)

    model_config = {"extra": "forbid"}

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not SlugPattern.match(value):
            raise ValueError(
                "name must be 1-40 chars and contain only letters, numbers, hyphen, or underscore"
            )
        return value

    @model_validator(mode="after")
    def _validate_graph(self) -> StrategyGraphConfig:
        ids = [node.id for node in self.strategies]
        duplicates = [node for node, count in Counter(ids).items() if count > 1]
        if duplicates:
            raise ValueError(f"strategy ids must be unique (duplicates: {duplicates})")

        if self.capital_policy.type == "fixed" and self.capital_policy.weights is not None:
            missing = set(ids) - set(self.capital_policy.weights.keys())
            if missing:
                raise ValueError(
                    f"fixed capital policy missing weights for strategies: {sorted(missing)}"
                )

        return self

    @classmethod
    def from_cli_defaults(
        cls,
        *,
        symbols: list[str],
        position_size: int,
        fast_period: int,
        slow_period: int,
    ) -> StrategyGraphConfig:
        node = StrategyNodeConfig(
            id="sma_default",
            type="sma",
            symbols=symbols,
            params={
                "fast_period": fast_period,
                "slow_period": slow_period,
                "position_size": position_size,
            },
            max_position=position_size,
        )
        return cls(strategies=[node])


def load_strategy_graph(path: Path) -> StrategyGraphConfig:
    """Load a strategy graph configuration from JSON."""
    raw = path.read_text()
    try:
        return StrategyGraphConfig.model_validate_json(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid strategy graph config at '{path}': {exc}") from exc
