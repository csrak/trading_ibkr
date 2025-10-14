# Simulation Harness & Strategy Examples

This document describes requirements for an order-book simulation environment and outlines the first two example strategies (low-cap options market maker and high-cap equity/options hybrid).

---

## 1. Simulation Harness Requirements

### 1.1 Goals
- Replay recorded market data (depth + trades) to evaluate strategies offline.
- Provide deterministic environments for unit/integration tests.
- Support hooks for strategy tuning (e.g., adjust spread width, inventory targets).

### 1.2 Core Components

1. **Event Stream Loader**
   - Reads recorded depth/trade events from Parquet/CSV using schemas defined in `data_schemas.md`.
   - Supports time slicing and symbol filtering.
   - Optional speed controls (real-time pace, accelerated, single-step).

2. **Market Simulator**
   - Maintains synthetic order book state from depth events.
   - Applies trade prints to update mid price/volume.
   - Provides APIs for best bid/ask, book imbalance, and other signals.

3. **Execution Simulator (Mock Broker)**
   - Accepts order instructions from the order manager.
   - Determines fill probabilities based on queue position and incoming trades.
   - Supports partial fills, cancellations, and latency modeling.
   - Emits fill events back to the strategy via an event bus.

4. **Strategy Runner**
   - Orchestrates the quoting engine, order manager, and risk module.
   - Steps through event stream, triggers quoting decisions, and processes fills.
   - Collects metrics (PnL, inventory, fill ratio, spread capture).

5. **Analytics Reporter**
   - Aggregates run metrics and generates summaries (tables, charts).
   - Exports datasets for further analysis.

### 1.3 Integration Points
- Reuse existing event bus for communication between mock broker and strategy.
- Plug into existing risk guard to enforce limits even in simulation.
- Provide CLI entry point, e.g., `ibkr-trader simulate --config configs/mm_low_cap.yaml`.

---

## 2. Example Strategies

### 2.1 Low-Capital Options Market Maker

**Objective**
- Maintain small, tight quotes on a single strike/expiry.
- Limit inventory drift and hedge with the underlying.

**Configuration**
- Underlying symbol (e.g., AAPL).
- Target option (strike, expiry).
- Quote size: small (e.g., 1–5 contracts).
- Spread: fixed or derived from implied vol (e.g., mid ± edge).
- Inventory bands: e.g., ±5 contracts.
- Hedge threshold: delta beyond 0.5 triggers underlying trade.
- Quote refresh rate: 1–2 seconds.

**Workflow**
1. Load option surface snapshot for theoretical price.
2. Start simulation with order book data for the option (if available) or the underlying.
3. Quoting engine generates bid/ask around mid price with inventory skew.
4. Order manager submits quotes; mock broker simulates fills.
5. Strategy hedges with underlying when delta limit exceeded.
6. Risk guard stops quoting if inventory or PnL breaches thresholds.

**Metrics**
- Spread capture per contract.
- Fill rate (quotes vs filled).
- Inventory distribution over time.
- Hedging efficiency (delta exposure post-hedge).

### 2.2 Higher-Capital Equity/Options Market Maker

**Objective**
- Quote multiple strikes/expiries with larger size and dynamic spreads.
- Manage cross-system inventory and hedge using multi-leg strategies.

**Configuration**
- Underlying symbol (e.g., SPY).
- Strike ladder (e.g., ATM ± 3 strikes) and near-term expiries.
- Layered quotes (two or more price levels per side).
- Spread model that widens/narrows based on realized volatility and order book depth.
- Inventory risk model with per-strike and aggregate limits.
- Hedging strategy: use spreads or multiple underlyings.
- Optional cross-venue quoting (SMART vs direct routes).

**Workflow**
1. Load option surface and order book depth for selected strikes.
2. Quoting engine computes theoretical prices using IV surface.
3. Generate layered quotes with skew adjustments (inventory, vol).
4. Order manager handles multiple working orders per strike.
5. Risk module aggregates inventory across strikes; triggers hedging.
6. Simulation tracks PnL, exposure, and compares against benchmarks.

**Metrics**
- PnL distribution and variance.
- Quote competitiveness (distance from best bid/ask).
- Inventory by strike/expiry.
- Execution quality (slippage vs theoretical).

---

## 3. Implementation Phases (Simulation + Examples)

1. **Harness MVP**
   - Implement event loader (depth + trades).
   - Build mock broker that fills orders based on simple rules (price crossing + random fills).
   - Integrate with existing strategy runner using stub quoting engine.

2. **Low-Cap Options MM Example**
   - Implement quoting engine with fixed spread + inventory skew.
   - Add order manager stub with basic place/cancel logic.
   - Validate via unit tests and simulation run over recorded data.

3. **Enhanced Simulator**
   - Support queue position modeling and latency.
   - Add metrics collection (PnL, inventory, fills).

4. **High-Cap Strategy**
   - Extend quoting engine for layered quotes and dynamic spreads.
   - Expand risk module for aggregate limits and complex hedging.
   - Provide configuration templates and CLI commands.

5. **Documentation & Tutorials**
   - Step-by-step guide on running simulations.
   - How-to for customizing strategy configs.
   - Recording and replaying live market data.

---

## 4. Data Requirements for Simulation

- Historical depth/trade data (Parquet) for at least one underlying and chosen options.
- Option surface snapshots aligned with simulation timestamps.
- Configuration files specifying simulation parameters (YAML/JSON).

---

## 5. Next Steps

1. Translate schemas into Python models (dataclasses/pydantic) for type safety.
2. Implement event loader + mock broker skeletons in code.
3. Draft configuration template for the low-cap options MM example.
4. Begin unit tests focusing on order manager interactions with the mock broker.

Once these foundations are in place, we can proceed with building actual strategies and verifying them through simulation runs.
