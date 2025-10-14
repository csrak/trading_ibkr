# Market Making Architecture Specification

This document captures the design required to support advanced market-making strategies (equities and options) within the IBKR Personal Trader platform. It is intended to guide implementation work in discrete, verifiable steps.

---

## 1. High-Level Goals

1. **Quoting Engine** for generating bid/ask orders around a theoretical price.
2. **Order Manager** responsible for quote lifecycle (place, modify, cancel) and venue interaction.
3. **Risk Guardrails** for inventory, PnL, and exposure limits with automated hedging triggers.
4. **Market Data Layer** capable of Level II depth (equities) and option surface snapshots.
5. **Simulation/Test Harness** for replaying order book events and validating strategies offline.

---

## 2. Components

### 2.1 Quoting Engine

**Responsibilities**
- Receive theoretical prices (from models or manual inputs).
- Calculate bid/ask quotes based on configured spreads, edge targets, inventory skew, and risk controls.
- Apply offsets for market microstructure factors (volatility, queue depth, order book imbalance).
- Support multiple quote styles: single-level, layered book, skewed for inventory control.

**Inputs**
- Theoretical mid price (from pricing models or IV surfaces).
- Market depth snapshot (top-of-book or multi-level).
- Current inventory and risk metrics (delta, gamma for options).
- Configured parameters:
  - Base spread or edge tolerance.
  - Inventory thresholds and skew functions.
  - Quote refresh rate and lifetime (time-in-force).
  - Minimum price increment and size increments.

**Outputs**
- Quote instructions (per side) including price, size, and metadata (inventory state, timestamp).

### 2.2 Order Manager

**Responsibilities**
- Translate quote instructions into IBKR orders (limit, marketable limit, pegged).
- Maintain order state (acknowledged, working, filled, cancelled).
- Handle re-quoting logic: cancel/replace when spreads change or inventory adjustments are required.
- Enforce throttle limits (max orders per second, in-flight orders).
- Provide hooks for risk guard (e.g., kill switch, max outstanding inventory).
- Support multi-venue or multi-strike contexts (for options).

**Key Behaviors**
- **PlaceQuote**(side, price, size, portfolio_state).
- **CancelQuote**(order_id, reason).
- **ModifyQuote** when underlying price moves or inventory adjustments needed.
- **HandleFillEvent**: update inventory, trigger hedging or stop quoting if thresholds breached.
- **Circuit Breakers**: stop quoting on certain events (PnL drawdown, connectivity loss, abnormal spread).

**Interactions**
- Broker interface (`IBKRBroker`) for actual order submissions.
- Quoting engine for new price/size instructions.
- Risk module for approvals/guardrails.
- Event bus for publishing fill reports, inventory updates, risk alerts.

### 2.3 Risk Module

**Metrics tracked**
- Position inventory (shares/contracts) and target inventory bands.
- Net exposure (per symbol, per option type).
- Delta/Gamma/Vega (options-specific).
- PnL (realized and unrealized).
- Spread capture metrics (quote fill price vs mid).

**Controls**
- Hard limits (stop quoting, flatten positions).
- Soft limits (reduce size, widen spreads).
- Hedging triggers (e.g., auto-hedge when delta exceeds threshold).
- Rate limits (max reorder frequency, order flow quotas).
- Circuit breakers based on volatility surges or order book anomalies.

### 2.4 Market Data

**Equities Level II**
- Depth snapshots (bid/ask levels with size, timestamp).
- Update frequency handling (tick by tick vs aggregated).
- Normalized schema for storing depth events (see Section 3).

**Options Surface**
- Bid/ask, last, greeks if available.
- Derived theoretical values (e.g., Black-Scholes) for use in quoting engine.
- Cache and offline storage for analysis/backtesting.

### 2.5 Simulation Harness

**Goals**
- Replay recorded order book events.
- Emulate fill logic (partial fills, queue position).
- Evaluate quoting strategies offline with identical APIs.
- Collect metrics: fill rate, inventory fluctuations, PnL distribution.

**Components**
- Event replay driver (reads recorded depth data).
- Mock broker executing orders based on market events.
- Strategy runner using the same quoting engine/order manager stack.

---

## 3. Data Schemas & Storage (Preview)

Detailed schema definitions will be captured in the next document. High-level requirements:

- **Order Book Levels**: `timestamp`, `symbol`, `side`, `price`, `size`, `level`.
- **Trade Events**: `timestamp`, `price`, `size`, `side` (aggressor), `venue`.
- **Option Surface**: `timestamp`, `symbol`, `expiry`, `strike`, `right`, `bid`, `ask`, `mid`, `implied_vol`.
- **Quote Instructions**: `timestamp`, `symbol`, `side`, `price`, `size`, `reason`.
- **Order States**: `order_id`, `status`, `filled_qty`, `avg_price`, `submit_time`, `cancel_time`.

Storage Options:
- CSV/Parquet for offline analysis.
- Optional MongoDB or SQLite for quick lookups (user preference).
- Ring buffers in memory for real-time calculations.

---

## 4. Strategy Examples

### 4.1 Low-Capital Options Market Maker (Example)
- Focus on a single underlying + limited strikes.
- Small, fixed quote sizes with tight inventory bounds.
- Auto-hedge using underlying stock when delta thresholds exceeded.
- Use yfinance/IBKR option chain for theoretical pricing.

### 4.2 Higher-Capital Equity/Options Market Maker
- Deploy layered quotes on multiple strikes and expiry buckets.
- Adaptive spread width based on realized volatility and queue depth.
- Cross-venue quoting (SMART routing vs direct exchange).
- Dynamic hedging strategy (e.g., use spreads, multi-leg trades).

---

## 5. Implementation Roadmap

1. **Schema Definition (Data Layer)**
   - Finalize classes for depth, trades, option surfaces.
   - Extend caching infrastructure to store Level II and option data.

2. **Order Manager Skeleton**
   - Define interfaces for place/modify/cancel.
   - Build a state machine to track working orders.
   - Implement throttle and circuit breakers.

3. **Quoting Engine MVP**
   - Support simple quoting around mid price with fixed spreads.
   - Integrate inventory skew and size adjustments.
   - Expose configuration via YAML or CLI options.

4. **Risk Guard Integration**
   - Hook inventory and PnL updates into guard rails.
   - Provide kill switch and auto-hedge triggers.

5. **Simulation Harness**
   - Implement replay driver and mock broker.
   - Enable strategy testing with recorded datasets.

6. **Examples & Tests**
   - Build low-cap options MM sample strategy using the new stack.
   - Create higher-cap equity/options mixed strategy example.
   - Comprehensive tests for order manager, quoting engine, and risk guard.

---

### Next Action Proposal

- Proceed to defining concrete data schemas for Level II books and option surfaces (`docs/market_making/data_schemas.md`).
- Begin architectural skeleton for the Order Manager in code (interfaces + stubs).

Feel free to append comments or edits as the implementation progresses.
