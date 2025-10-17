from __future__ import annotations

from decimal import Decimal

from ibkr_trader.strategy_configs.graph import StrategyGraphConfig, StrategyNodeConfig
from ibkr_trader.strategy_coordinator.policies import EqualWeightPolicy, PositionEnvelope


def test_equal_weight_policy_uses_node_limits() -> None:
    graph = StrategyGraphConfig(
        strategies=[
            StrategyNodeConfig(id="s1", type="sma", symbols=["AAPL"], max_position=5),
            StrategyNodeConfig(id="s2", type="sma", symbols=["MSFT"], max_notional=Decimal("1000")),
        ]
    )

    policy = EqualWeightPolicy(graph.capital_policy)
    policy.prepare(graph)

    env1 = policy.envelope_for("s1", "AAPL")
    assert env1.max_position == 5
    assert env1.max_notional is None

    env2 = policy.envelope_for("s2", "MSFT")
    assert env2.max_notional == Decimal("1000")
    assert env2.max_position is None

    env_missing = policy.envelope_for("unknown", "GOOG")
    assert isinstance(env_missing, PositionEnvelope)
    assert env_missing.max_position is None
    assert env_missing.max_notional is None
