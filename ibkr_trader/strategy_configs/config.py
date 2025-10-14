"""Pydantic models describing strategy configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, ValidationError

type StrategyType = Literal["fixed_spread_mm", "vol_overlay"]


class DataConfig(BaseModel):
    order_book: list[Path] = Field(default_factory=list)
    trades: list[Path] = Field(default_factory=list)
    option_surface: list[Path] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class ExecutionConfig(BaseModel):
    spread: float | None = None
    quote_size: int = 1
    volatility_target: float | None = None
    leverage_cap: float | None = None

    model_config = {"extra": "forbid"}


class RiskConfig(BaseModel):
    inventory_limit: int = 5
    max_drawdown: float | None = None
    kill_switch: bool = True

    model_config = {"extra": "forbid"}


class StrategyConfig(BaseModel):
    name: str = "strategy"
    strategy_type: StrategyType
    symbol: str
    data: DataConfig = Field(default_factory=DataConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)

    model_config = {"extra": "forbid"}

    REGISTRY: ClassVar[dict[str, type[StrategyConfig]]] = {}

    def dump_json(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2))

    @classmethod
    def register(cls, config_type: type[StrategyConfig]) -> None:
        strategy_field = config_type.model_fields.get("strategy_type")
        if strategy_field is None or strategy_field.default is None:
            raise ValueError(f"Config {config_type.__name__} must define a strategy_type default")
        cls.REGISTRY[strategy_field.default] = config_type

    @classmethod
    def load(cls, path: Path) -> StrategyConfig:
        data = json.loads(path.read_text())
        strategy_type = data.get("strategy_type")
        if strategy_type not in cls.REGISTRY:
            raise ValueError(f"Unknown strategy_type '{strategy_type}'")
        config_cls = cls.REGISTRY[strategy_type]
        try:
            return config_cls.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"Invalid configuration: {exc}") from exc


class FixedSpreadMMConfig(StrategyConfig):
    strategy_type: Literal["fixed_spread_mm"] = "fixed_spread_mm"
    execution: ExecutionConfig = Field(
        default_factory=lambda: ExecutionConfig(spread=0.1, quote_size=1)
    )


class VolOverlayExecutionConfig(ExecutionConfig):
    volatility_target: float = 0.1
    conviction_signal: str | None = None
    lookback_window: int = 20


class VolatilityOverlayConfig(StrategyConfig):
    strategy_type: Literal["vol_overlay"] = "vol_overlay"
    execution: VolOverlayExecutionConfig = Field(default_factory=VolOverlayExecutionConfig)


StrategyConfig.register(FixedSpreadMMConfig)
StrategyConfig.register(VolatilityOverlayConfig)


def load_strategy_config(path: Path) -> StrategyConfig:
    return StrategyConfig.load(path)
