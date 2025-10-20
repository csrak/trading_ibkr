from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from ibkr_trader.strategy_configs import (
    CapitalPolicyConfig,
    StrategyGraphConfig,
    StrategyNodeConfig,
)
from ibkr_trader.strategy_configs.config import FixedSpreadMMConfig


def _write_sample_strategy_config(path: Path) -> None:
    config = FixedSpreadMMConfig(strategy_type="fixed_spread_mm", symbol="AAPL")
    config.dump_json(path)


def test_from_cli_defaults_normalizes_symbols() -> None:
    graph = StrategyGraphConfig.from_cli_defaults(
        symbols=["aapl", "msft", "AAPL"],
        position_size=10,
        fast_period=5,
        slow_period=15,
    )

    assert len(graph.strategies) == 1
    node = graph.strategies[0]
    assert node.id == "sma_default"
    assert node.type == "sma"
    assert node.symbols == ["AAPL", "MSFT"]
    assert node.max_position == 10
    assert node.params["fast_period"] == 5
    assert node.params["slow_period"] == 15


def test_strategy_ids_must_be_unique() -> None:
    with pytest.raises((ValidationError, ValueError)) as exc_info:
        StrategyGraphConfig(
            strategies=[
                StrategyNodeConfig(id="dup", type="sma", symbols=["AAPL"]),
                StrategyNodeConfig(id="dup", type="sma", symbols=["MSFT"]),
            ]
        )

    assert "duplicates" in str(exc_info.value)


def test_config_adapter_requires_valid_config(tmp_path: Path) -> None:
    config_path = tmp_path / "strategy.json"
    _write_sample_strategy_config(config_path)

    node = StrategyNodeConfig(
        id="adapter",
        type="config_adapter",
        symbols=["AAPL"],
        config_path=config_path,
    )

    graph = StrategyGraphConfig(strategies=[node])
    assert graph.strategies[0].config_path == config_path


def test_config_adapter_missing_file_errors(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.json"

    with pytest.raises(ValidationError) as exc_info:
        StrategyNodeConfig(
            id="adapter",
            type="config_adapter",
            symbols=["AAPL"],
            config_path=missing_path,
        )

    assert "Invalid strategy config" in str(exc_info.value)


@pytest.mark.parametrize(
    "weights,total_valid",
    [
        ({"sma": Decimal("0.6"), "mr": Decimal("0.3")}, True),
        ({"sma": Decimal("1.2")}, False),
    ],
)
def test_fixed_policy_weight_validation(weights: dict[str, Decimal], total_valid: bool) -> None:
    if total_valid:
        policy = CapitalPolicyConfig(type="fixed", weights=weights)
        assert policy.type == "fixed"
    else:
        with pytest.raises(ValidationError):
            CapitalPolicyConfig(type="fixed", weights=weights)
