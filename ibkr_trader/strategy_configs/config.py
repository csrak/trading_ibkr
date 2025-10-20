"""Pydantic models describing strategy configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, ValidationError

type StrategyType = Literal[
    "fixed_spread_mm",
    "vol_overlay",
    "mean_reversion",
    "skew_arb",
    "microstructure_ml",
    "regime_rotation",
    "vol_spillover",
]


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

    @classmethod
    def build_from_type(cls, strategy_type: str, data: dict[str, object]) -> StrategyConfig:
        config_cls = cls.REGISTRY.get(strategy_type)
        if config_cls is None:
            raise ValueError(f"Unknown strategy_type '{strategy_type}'")
        payload = {"strategy_type": strategy_type, **data}
        try:
            return config_cls.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"Invalid configuration payload: {exc}") from exc


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


class MeanReversionExecutionConfig(ExecutionConfig):
    lookback_short: int = 20
    lookback_long: int = 60
    entry_zscore: float = 2.0
    exit_zscore: float = 0.5
    volatility_window: int = 30
    stop_multiple: float = 2.0


class MeanReversionConfig(StrategyConfig):
    strategy_type: Literal["mean_reversion"] = "mean_reversion"
    execution: MeanReversionExecutionConfig = Field(default_factory=MeanReversionExecutionConfig)


class SkewArbExecutionConfig(ExecutionConfig):
    expiries: list[str] = Field(default_factory=list)
    strikes_per_expiry: int = 3
    skew_threshold: float = 0.15
    max_notional: float = 1000.0
    min_open_interest: int = 100


class SkewArbitrageConfig(StrategyConfig):
    strategy_type: Literal["skew_arb"] = "skew_arb"
    execution: SkewArbExecutionConfig = Field(default_factory=SkewArbExecutionConfig)


class MicrostructureExecutionConfig(ExecutionConfig):
    model_path: Path | None = None
    feature_set: list[str] = Field(default_factory=list)
    prediction_horizon_ms: int = 1000
    confidence_threshold: float = 0.6


class MicrostructureMLConfig(StrategyConfig):
    strategy_type: Literal["microstructure_ml"] = "microstructure_ml"
    execution: MicrostructureExecutionConfig = Field(default_factory=MicrostructureExecutionConfig)


class RegimeRotationExecutionConfig(ExecutionConfig):
    regime_feature: str = "volatility"
    regime_window: int = 60
    rebalance_frequency: str = "weekly"
    target_allocations: dict[str, dict[str, float]] = Field(default_factory=dict)


class RegimeRotationConfig(StrategyConfig):
    strategy_type: Literal["regime_rotation"] = "regime_rotation"
    execution: RegimeRotationExecutionConfig = Field(default_factory=RegimeRotationExecutionConfig)


class VolSpilloverExecutionConfig(ExecutionConfig):
    asset_pairs: list[list[str]] = Field(default_factory=list)
    spillover_threshold: float = 0.2
    hedge_ratio: float = 1.0
    correlation_window: int = 60


class VolSpilloverConfig(StrategyConfig):
    strategy_type: Literal["vol_spillover"] = "vol_spillover"
    execution: VolSpilloverExecutionConfig = Field(default_factory=VolSpilloverExecutionConfig)


StrategyConfig.register(FixedSpreadMMConfig)
StrategyConfig.register(VolatilityOverlayConfig)
StrategyConfig.register(MeanReversionConfig)
StrategyConfig.register(SkewArbitrageConfig)
StrategyConfig.register(MicrostructureMLConfig)
StrategyConfig.register(RegimeRotationConfig)
StrategyConfig.register(VolSpilloverConfig)


def load_strategy_config(path: Path) -> StrategyConfig:
    return StrategyConfig.load(path)
