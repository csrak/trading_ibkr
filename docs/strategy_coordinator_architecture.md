# Strategy Coordinator Architecture Draft

## Purpose

Introduce a coordination layer that can run multiple live strategies concurrently, share infrastructure (`EventBus`, broker, risk overlays), and allocate capital safely. This unlocks combining existing SMA and model-driven strategies today while paving the way for regime-aware adapters and future ML integrations.

## Scope

- **Live & paper trading** orchestrator (not backtest-only).
- Manage strategy lifecycle, subscriptions, and graceful shutdown.
- Provide capital allocation hooks and aggregate risk/safety enforcement.
- Remain backwards compatible with the current single-strategy CLI workflow.

## Core Responsibilities

1. **Lifecycle management**
   - Instantiate strategies from config, inject shared dependencies.
   - Start/stop strategies and ensure background tasks are cancelled on exit.
   - Surface startup errors early with clear logging.

2. **Capital allocation**
   - Expose pluggable policies (equal weight, fixed size, custom) to derive per-strategy position sizing envelopes.
   - Track allocated/available capital so strategies remain within guardrails.

3. **Unified safety layer**
   - Route all order intents through `RiskGuard`/`LiveTradingGuard`.
   - Maintain aggregate exposure and daily loss ceilings across strategies.
   - Provide hooks for future sector / asset-class caps.

4. **Market data coordination**
   - Register required symbol subscriptions once per symbol.
   - Share latest prices across strategies when beneficial (e.g., via coordinator cache).

5. **Telemetry & diagnostics**
   - Emit per-strategy performance snapshots (PnL, exposure, signal state) to telemetry bus.
   - Bubble up health signals (stalls, repeated errors, subscription drops).

6. **Strategy isolation**
   - Encapsulate strategy-specific state to avoid cross-talk.
   - Provide well-defined interfaces for strategies to request orders or data so coordinator can mediate.

## High-Level Design

```
                 ┌────────────────┐
                 │  CLI (run)     │
                 └──────┬─────────┘
                        │ load StrategyGraphConfig
                        ▼
              ┌─────────────────────┐
              │ StrategyCoordinator │
              ├─────────────────────┤
              │ - strategies[]      │
              │ - capital_policy    │
              │ - market_cache      │
              │ - safety_context    │
              └──────┬──────────────┘
         market data │        order intents
                     ▼
       ┌───────────────────────┐         ┌───────────────┐
       │ EventBus (MARKET_DATA)│◄────────┤MarketDataService│
       └───────────────────────┘         └───────────────┘
              ▲                                  │
              │  executions/diagnostics          │ IBKR / Sim
              │                                  ▼
        ┌──────────┐                      ┌────────────┐
        │ Strategies│                      │ IBKRBroker │
        └──────────┘                      └────────────┘
```

### Coordinator Components

- `StrategyCoordinator`
  - Accepts `StrategyGraphConfig`, broker, event bus, risk guard, telemetry reporter.
  - Owns async task supervising strategy event loops.
  - Maintains registry of active strategies and their capital allocations.

- `CapitalAllocationPolicy` (protocol/abstract base)
  - Method `allocate(symbol: str, strategy_id: str) -> PositionEnvelope`.
  - Default implementation: equal per-strategy share of configured position size.
  - Future: volatility targeting, Kelly sizing, regime-aware adjustments.

- `PositionEnvelope`
  - Dataclass with `max_size`, `max_notional`, optional soft/hard limits.
  - Passed into strategies so they size orders within bounds without manual config tweaks.

- `StrategyWrapper`
  - Bridges existing `Strategy` API with coordinator handshakes.
  - Tracks last heartbeat, outstanding orders, and realized PnL (pulling from `PortfolioState`).

### Safety Integration

- Coordinator receives order intents (`OrderRequest`) from strategies through a mediated channel.
- Before forwarding to broker:
  1. Merge intent with envelope bounds (clip sizes).
  2. Call `RiskGuard.validate_order`.
  3. Ensure `LiveTradingGuard` acknowledgement still valid.
  4. Track cumulative exposure for each symbol + overall.
- On order status / execution events, coordinator updates internal exposure state and notifies strategies if adjustments are needed.

### Market Data Handling

- Coordinator builds a symbol map → subscriber count; only first strategy triggers `MarketDataService.subscribe`, reuse subscription for others.
- Optionally expose a shared price cache so strategies can query latest price without storing duplicates.
- For mock data runs, coordinator uses the existing publish loop but allows per-strategy offsets (for e.g., feature warmups).

### Telemetry

- Extend telemetry reporter events with:
  - `strategy.lifecycle` (start/stop, errors)
  - `strategy.metrics` (position, realized/unrealized PnL)
  - `strategy.coordinator` (capital allocation decisions, exposure warnings)
- Ensure telemetry respects existing JSONL schema to avoid downstream breakage.

## Backwards Compatibility

- Default CLI path (no `--config`) creates a single-strategy graph containing SMA strategy.
- Maintain existing CLI flags for quick experiments; coordinator reads them as overrides for default graph config.
- Rolling feature toggle via config (`IBKR_ENABLE_STRATEGY_COORDINATOR`) to allow gradual adoption if needed.

## Module Layout Plan

To keep files focused and maintainable:

- `ibkr_trader/strategy_coordinator/__init__.py` – package exposing public coordinator API.
- `ibkr_trader/strategy_coordinator/coordinator.py` – main `StrategyCoordinator` implementation (≤ ~250 lines).
- `ibkr_trader/strategy_coordinator/policies.py` – capital allocation policies + `PositionEnvelope` dataclasses.
- `ibkr_trader/strategy_coordinator/wrapper.py` – strategy wrapper/adapter utilities.
- `ibkr_trader/strategy_coordinator/errors.py` – custom exception types.
- `ibkr_trader/strategy_configs/graph.py` – Pydantic graph configuration models.
- `tests/strategy_coordinator/` – unit/integration tests partitioned per component (coordinator, policies, config validation).

The existing `ibkr_trader/strategy_configs/config.py` remains focused on single-strategy configs. `graph.py` will import from `config.py` when referencing registered strategy types, preventing circular imports by keeping coordinator package independent of concrete strategy implementations.

## Implementation Roadmap (Scaffolding)

1. **Config layer**
   - Implement `StrategyGraphConfig` models in `strategy_configs/graph.py`, including validators and helper constructors (e.g., `from_cli_defaults`).
   - Extend CLI loader to detect graph configs while preserving legacy single-strategy behavior.

2. **Coordinator scaffold**
   - Create `StrategyCoordinator` class with constructor wiring (`broker`, `event_bus`, `risk_guard`, `market_data`, `telemetry`).
   - Add lifecycle methods: `start(graph_config)`, `stop()`, `_run()`.
   - Provide interface for strategies to submit order intents (`submit_order(request, strategy_id)`).

3. **Capital policy module**
   - Define `CapitalAllocationPolicy` protocol/ABC with `prepare(graph_config)` and `envelope_for(strategy_id, symbol)` methods.
   - Implement `EqualWeightPolicy` as MVP; stub out `FixedPolicy` for future use.

4. **Strategy wrappers**
   - Build lightweight wrapper translating between coordinator envelopes and existing strategies.
   - Hook into market data cache (optional MVP: rely on existing strategy state, add cache later).

5. **Integration point**
   - Update `cli_commands/trading.py` to instantiate coordinator when graph configs are used.
   - Ensure graceful shutdown path cancels coordinator, unsubscribes market data once.

Each phase should land in manageable PR-sized chunks (<400 LOC) with focused tests.

## Testing & Documentation Plan

- **Unit tests**
  - `tests/strategy_coordinator/test_graph_config.py`: schema validation, CLI defaults, error cases.
  - `tests/strategy_coordinator/test_policies.py`: allocation math, envelope clipping.
  - `tests/strategy_coordinator/test_coordinator.py`: lifecycle start/stop, market data subscription dedupe (with mocks).

- **Async integration smoke**
  - Use `SimulatedBroker` + `SimulatedMarketData` to run two dummy strategies through coordinator; assert order intents routed and `RiskGuard` invoked once per order.

- **Regression**
  - Re-run existing CLI tests to guarantee legacy path unaffected.
  - Add targeted mypy module coverage for new package (protocol adherence).

- **Documentation**
  - Update `README.md` strategy section with coordinator overview and sample graph config snippet.
  - Extend `QUICKSTART.md` with instructions for running multiple strategies (paper mode).
  - Add `docs/strategy_coordinator_architecture.md` reference to `docs/README.md` (if present) for discoverability.

- **Operational Notes**
  - Include telemetry field additions in `docs/telemetry.md` (create if missing).
  - Record migration guide snippet in `PROGRESS.md` or `PHASE2_PLAN.md` summarizing rollout steps.

## Open Questions

1. **Order intent API**: do we standardize on `OrderRequest` or introduce a higher-level signal message (e.g., desired target position)?
2. **Strategy errors**: should coordinator restart failed strategies automatically or bubble up and halt the run?
3. **Capital policy inputs**: do we integrate portfolio metrics (e.g., net liquidation) dynamically or rely on static configs?
4. **Telemetry volume**: best cadence for metrics without bloating log files during long sessions?

These will be resolved during implementation planning. For now, the coordinator will err on the side of conservative safety—halt on unhandled strategy exceptions and require explicit config for non-trivial capital policies.

## Order Intent Channel (Design)

To decouple strategies from direct `OrderRequest` creation, the coordinator will introduce a typed intent workflow:

- **OrderIntent** dataclass (module `ibkr_trader/order_intents.py`)
  - Fields: `strategy_id`, `symbol`, `intent_type` (`"target_position"` | `"market_delta"` initially), `quantity`, optional `side`, `timestamp`, and optional `metadata` dict.
  - `intent_type="target_position"` expresses desired absolute exposure; coordinator computes delta vs. current position and issues an order if needed.
  - `intent_type="market_delta"` issues an immediate delta trade (useful for legacy strategies in transition).

- **Strategy API additions**
  - `Strategy` base class gains helper `submit_target_position(symbol, target, metadata=None)` which publishes an `OrderIntent` to a coordinator-owned queue.
  - Existing `place_market_order` remains for backward compatibility; default SMA strategy will migrate to `submit_target_position`.

- **Coordinator processing**
  - `StrategyCoordinator` hosts an internal async loop reading intents from an `asyncio.Queue`.
  - Before forwarding to broker, coordinator:
    1. Resolves current position via cached exposures.
    2. Calculates desired delta (respecting envelopes), clips if needed, and emits telemetry on clip or final delta.
    3. Delegates to existing `CoordinatorBrokerProxy` for risk validation + execution.
  - Empty/zero deltas short-circuit with telemetry note (`coordinator.intent_ignored`).
  - Signed exposure is cached per symbol; aggregate notional updates are sent to telemetry so `RiskGuard` can enforce portfolio-wide limits.

- **Telemetry**
  - `coordinator.intent_received` logs every intent with requested vs. resolved quantities.
  - `coordinator.intent_fulfilled` confirms execution path (order id, delta).
  - Clip warnings reuse existing `order_clipped` event types.

- **Migration path**
  1. Implement intent infrastructure alongside current order calls.
  2. Migrate SMA strategy to intents (feature-flagged to allow rollback).
  3. Audit other strategies and move them to target-position semantics.
  4. Eventually demote direct broker access from strategies to guarded internal use only.

- **Testing**
  - Unit tests for intent queue handling, delta math (including envelope interactions), and telemetry assertions.
  - Integration test: dummy strategy posts alternating target positions; verify resulting orders and exposures.

This approach keeps strategy code focused on desired exposure while the coordinator centralizes safety, netting, and broker interactions.

## Configuration Sketch

Initial Pydantic models (`ibkr_trader/strategy_configs/graph.py`):

```python
class StrategyGraphConfig(BaseModel):
    name: str = Field(default="default_graph")
    strategies: list["StrategyNodeConfig"]
    capital_policy: "CapitalPolicyConfig" = Field(
        default_factory=lambda: CapitalPolicyConfig(type="equal_weight")
    )
    settings: GraphRuntimeSettings = GraphRuntimeSettings()


class StrategyNodeConfig(BaseModel):
    id: str
    type: Literal["sma", "industry_model", "config_adapter"]
    symbols: list[str]
    params: dict[str, Any] = Field(default_factory=dict)
    max_position: int | None = None
    max_notional: Decimal | None = None
    warmup_bars: int = 0


class CapitalPolicyConfig(BaseModel):
    type: Literal["equal_weight", "fixed", "vol_target"]
    weights: dict[str, Decimal] | None = None  # when type == "fixed"
    target_vol: Decimal | None = None          # when type == "vol_target"


class GraphRuntimeSettings(BaseModel):
    allow_partial_start: bool = False
    heartbeat_timeout_seconds: int = 30
    telemetry_interval_seconds: int = 60
```

### Validation Rules

- Strategy IDs must be unique and slug-safe.
- Symbols normalized to upper-case; coordinator deduplicates across strategies.
- At least one strategy required; CLI fallback generates a single SMA node with user CLI parameters.
- `max_position` / `max_notional` default to config-level position sizing if unspecified; validated against hard safety limits from `IBKRConfig`.
- Capital policy constraints:
  - `equal_weight`: `weights` and `target_vol` must be `None`.
  - `fixed`: `weights` required, sum within `(0, 1]`; coordinator warns if sum < 1 (unallocated capital).
  - `vol_target`: requires `target_vol` plus per-strategy historical volatility lookback definition (future enhancement).
- Warmup bars capped (e.g., <= 5000) to avoid runaway memory usage.
- Heartbeat timeout minimum 5 seconds; telemetry interval minimum 10 seconds.

Validation errors surface via CLI before any connections are opened, keeping failure fast and safe.
