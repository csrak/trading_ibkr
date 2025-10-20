"""Wrappers that adapt concrete strategies for coordinator execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ibkr_trader.base_strategy import BrokerProtocol
from ibkr_trader.events import EventBus
from ibkr_trader.portfolio import RiskGuard
from ibkr_trader.strategy import SimpleMovingAverageStrategy, SMAConfig, Strategy
from ibkr_trader.strategy_adapters import ConfigBasedLiveStrategy
from ibkr_trader.strategy_configs.config import StrategyConfig
from ibkr_trader.strategy_configs.factory import StrategyFactory
from ibkr_trader.strategy_configs.graph import StrategyNodeConfig


class StrategyHandle(Protocol):
    """Minimal lifecycle interface exposed to the coordinator."""

    id: str

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


@dataclass(slots=True)
class StrategyWrapper:
    """Binds an instantiated strategy with its node metadata."""

    id: str
    node: StrategyNodeConfig
    impl: Strategy

    async def start(self) -> None:
        await self.impl.start()

    async def stop(self) -> None:
        await self.impl.stop()


def build_strategy_wrapper(
    *,
    node: StrategyNodeConfig,
    broker: BrokerProtocol,
    event_bus: EventBus,
    risk_guard: RiskGuard | None,
) -> StrategyWrapper:
    """Construct a strategy wrapper for a node config."""

    if node.type == "sma":
        config = SMAConfig(
            name=f"SMA_{node.id}",
            symbols=node.symbols,
            position_size=node.params.get("position_size", node.max_position or 10),
            fast_period=node.params.get("fast_period", 10),
            slow_period=node.params.get("slow_period", 20),
        )
        strategy = SimpleMovingAverageStrategy(
            config=config,
            broker=broker,
            event_bus=event_bus,
            risk_guard=risk_guard,
        )
        return StrategyWrapper(id=node.id, node=node, impl=strategy)

    if node.type == "config_adapter":
        assert node.config_path is not None  # validated earlier
        strategy_config = StrategyConfig.load(node.config_path)
        replay_strategy = StrategyFactory.create(strategy_config)
        live_adapter = ConfigBasedLiveStrategy(
            impl=replay_strategy,
            broker=broker,
            event_bus=event_bus,
            symbol=strategy_config.symbol,
        )
        return StrategyWrapper(id=node.id, node=node, impl=live_adapter)

    # Fall back to registered strategy configs via factory
    config_data: dict[str, object] = dict(node.params)
    config_data.setdefault("name", node.id)
    if node.symbols:
        config_data.setdefault("symbol", node.symbols[0])
    strategy_config = StrategyConfig.build_from_type(node.type, config_data)
    replay_strategy = StrategyFactory.create(strategy_config)
    live_adapter = ConfigBasedLiveStrategy(
        impl=replay_strategy,
        broker=broker,
        event_bus=event_bus,
        symbol=strategy_config.symbol,
    )
    return StrategyWrapper(id=node.id, node=node, impl=live_adapter)

    raise ValueError(f"Unsupported strategy node type '{node.type}'")
