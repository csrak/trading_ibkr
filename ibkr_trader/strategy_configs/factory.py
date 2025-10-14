"""Strategy factory for constructing strategy instances from configs."""

from __future__ import annotations

from collections.abc import Callable

from ibkr_trader.sim.runner import ReplayStrategy
from ibkr_trader.sim.strategies import FixedSpreadMMStrategy

from .config import FixedSpreadMMConfig, StrategyConfig, VolatilityOverlayConfig

FactoryFn = Callable[[StrategyConfig], ReplayStrategy]


class StrategyFactory:
    _registry: dict[str, FactoryFn] = {}

    @classmethod
    def register(cls, strategy_type: str, factory_fn: FactoryFn) -> None:
        cls._registry[strategy_type] = factory_fn

    @classmethod
    def create(cls, config: StrategyConfig) -> ReplayStrategy:
        factory = cls._registry.get(config.strategy_type)
        if factory is None:
            raise ValueError(f"No strategy factory registered for type '{config.strategy_type}'")
        return factory(config)


def _create_fixed_spread_mm(config: StrategyConfig) -> ReplayStrategy:
    cfg = FixedSpreadMMConfig.model_validate(config.model_dump())
    return FixedSpreadMMStrategy(
        symbol=cfg.symbol,
        quote_size=cfg.execution.quote_size,
        spread=cfg.execution.spread or 0.1,
        inventory_limit=cfg.risk.inventory_limit,
    )


def _create_vol_overlay(config: StrategyConfig) -> ReplayStrategy:
    cfg = VolatilityOverlayConfig.model_validate(config.model_dump())

    class StubVolOverlayStrategy(ReplayStrategy):
        def __init__(self) -> None:
            self.parameters = cfg.execution

    return StubVolOverlayStrategy()


StrategyFactory.register("fixed_spread_mm", _create_fixed_spread_mm)
StrategyFactory.register("vol_overlay", _create_vol_overlay)
