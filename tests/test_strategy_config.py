from __future__ import annotations

from pathlib import Path

import pytest

from ibkr_trader.strategy_configs import (
    FixedSpreadMMConfig,
    MeanReversionConfig,
    MicrostructureMLConfig,
    RegimeRotationConfig,
    SkewArbitrageConfig,
    StrategyConfig,
    StrategyFactory,
    VolatilityOverlayConfig,
    VolSpilloverConfig,
)


def test_strategy_config_round_trip(tmp_path: Path) -> None:
    config = FixedSpreadMMConfig(
        name="mm",
        symbol="AAPL",
        execution={"spread": 0.25, "quote_size": 2},
        risk={"inventory_limit": 3},
        data={"order_book": [tmp_path / "ob.csv"]},
    )

    path = tmp_path / "config.json"
    config.dump_json(path)

    loaded = StrategyConfig.load(path)

    assert isinstance(loaded, FixedSpreadMMConfig)
    assert loaded.execution.spread == 0.25
    assert loaded.risk.inventory_limit == 3
    assert loaded.data.order_book[0] == tmp_path / "ob.csv"


def test_strategy_factory_creates_fixed_spread_strategy() -> None:
    config = FixedSpreadMMConfig(symbol="AAPL", execution={"spread": 0.15, "quote_size": 1})
    strategy = StrategyFactory.create(config)
    from ibkr_trader.sim.strategies import FixedSpreadMMStrategy

    assert isinstance(strategy, FixedSpreadMMStrategy)


def test_vol_overlay_config_loads() -> None:
    config = VolatilityOverlayConfig(symbol="SPY", execution={"volatility_target": 0.12})
    strategy = StrategyFactory.create(config)
    assert strategy.parameters.volatility_target == 0.12


def test_additional_strategy_configs_create_stub_strategies() -> None:
    configs = [
        MeanReversionConfig(symbol="AAPL"),
        SkewArbitrageConfig(symbol="AAPL", execution={"expiries": ["2024-01-19"]}),
        MicrostructureMLConfig(symbol="AAPL", execution={"model_path": "models/mm.onnx"}),
        RegimeRotationConfig(
            symbol="SPY", execution={"target_allocations": {"bull": {"SPY": 0.7, "TLT": 0.3}}}
        ),
        VolSpilloverConfig(symbol="VIX", execution={"asset_pairs": [["SPY", "VIX"]]}),
    ]

    for cfg in configs:
        strategy = StrategyFactory.create(cfg)
        assert hasattr(strategy, "config")
        assert strategy.config.strategy_type == cfg.strategy_type


def test_unknown_strategy_type_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"strategy_type": "unknown", "symbol": "AAPL"}')

    with pytest.raises(ValueError):
        StrategyConfig.load(path)
