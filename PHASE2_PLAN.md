# Phase 2: Advanced Order Types & Risk Management

**Status:** Planning
**Start Date:** 2025-10-17
**Target:** 2-3 sessions with zero tech debt accumulation

---

## Goals

1. **Advanced Order Types:** Bracket orders, trailing stops, OCO orders
2. **Enhanced Risk Management:** Per-symbol limits, correlation-based portfolio risk
3. **Order Management UI:** View, modify, cancel orders from dashboard
4. **Zero Tech Debt:** Tests, documentation, and type safety maintained throughout

---

## Phase 2 Breakdown

### Step 1: Bracket Orders (Session 1, Part 1)

**Goal:** Implement parent-child order relationships for automatic stop loss + take profit

**Technical Design:**

```python
@dataclass
class BracketOrderRequest:
    """Request for bracket order (entry + stop loss + take profit)."""

    parent: OrderRequest  # Entry order (MARKET or LIMIT)
    stop_loss: OrderRequest  # Stop loss (STOP order)
    take_profit: OrderRequest  # Take profit (LIMIT order)

    def validate(self) -> None:
        """Ensure stop/take profit are on opposite sides from parent."""
        # Parent BUY -> Stop/TP are SELL
        # Parent SELL -> Stop/TP are BUY
```

**Implementation Tasks:**
1. Add `BracketOrderRequest` to `models.py`
2. Extend `IBKRBroker.place_bracket_order()` method
3. Track parent-child relationships in `PortfolioState`
4. Add event handling for child order activation
5. CLI command: `ibkr-trader bracket-order --symbol AAPL --quantity 10 --stop-loss 145 --take-profit 155`

**Risk Considerations:**
- Validate that stop loss is on correct side of entry
- Ensure child orders cancel if parent order rejected
- Handle partial fills (scale child order quantities)
- Test with paper trading thoroughly before live

**Tests Required:**
- `test_bracket_order_validation()` - Input validation
- `test_bracket_order_parent_child()` - Relationship tracking
- `test_bracket_order_parent_rejection()` - Child cancellation
- `test_bracket_order_partial_fill()` - Quantity scaling

**Documentation:**
- Add bracket order section to strategy guide
- Update CLI reference with new command
- Add example workflow to examples/

**Tech Debt Prevention:**
- ✅ Tests written alongside implementation
- ✅ Type hints with mypy strict mode
- ✅ Documentation updated immediately

---

### Step 2: Trailing Stops (Session 1, Part 2)

**Goal:** Dynamic stop loss that follows price movement

**Technical Design:**

```python
@dataclass
class TrailingStopConfig:
    """Configuration for trailing stop order."""

    symbol: str
    side: OrderSide  # SELL for long positions, BUY for shorts
    quantity: int
    trail_amount: Decimal  # Dollar amount OR
    trail_percent: Decimal | None  # Percentage (mutually exclusive)
    activation_price: Decimal | None  # Optional activation threshold

class TrailingStopManager:
    """Manages trailing stop orders with dynamic adjustment."""

    def __init__(self, event_bus: EventBus, broker: IBKRBroker):
        self.active_trailing_stops: dict[str, TrailingStop] = {}

    async def create_trailing_stop(self, config: TrailingStopConfig) -> str:
        """Create and activate trailing stop."""

    async def _on_market_data(self, symbol: str, price: Decimal) -> None:
        """Update stop loss if price moved favorably."""
        # For long: if price increased, raise stop loss
        # For short: if price decreased, lower stop loss
```

**Implementation Tasks:**
1. Add `TrailingStopConfig` to `models.py`
2. Create `TrailingStopManager` in new file `ibkr_trader/trailing_stops.py`
3. Subscribe to `MARKET_DATA` events for price tracking
4. Implement dynamic stop adjustment logic
5. Persist trailing stop state to disk (survive restarts)
6. CLI command: `ibkr-trader trailing-stop --symbol AAPL --quantity 10 --trail-percent 2.0`

**Risk Considerations:**
- Rate limit IBKR order modifications (max 1 per second per symbol)
- Handle gaps (avoid adjusting stop past current price)
- Ensure trailing stop cancels if position closed manually
- Test with volatile stocks (NVDA, TSLA) in paper mode

**Tests Required:**
- `test_trailing_stop_long_position()` - Stop raises with price
- `test_trailing_stop_short_position()` - Stop lowers with price
- `test_trailing_stop_does_not_widen()` - Stop never moves against position
- `test_trailing_stop_activation_threshold()` - Only activates above threshold
- `test_trailing_stop_persistence()` - Survives process restart

**Documentation:**
- Add trailing stop section to strategy guide
- Document rate limiting behavior
- Add examples with different trail configurations

**Tech Debt Prevention:**
- ✅ State persistence prevents data loss on restart
- ✅ Rate limiting prevents IBKR throttling
- ✅ Comprehensive edge case testing

---

### Step 3: OCO Orders (Session 2, Part 1)

**Goal:** One-Cancels-Other orders (if one fills, cancel the other)

**Technical Design:**

```python
@dataclass
class OCOOrderRequest:
    """One-Cancels-Other order pair."""

    order_a: OrderRequest
    order_b: OrderRequest
    group_id: str  # Unique identifier for OCO pair

class OCOOrderManager:
    """Manages OCO order pairs."""

    def __init__(self, event_bus: EventBus, broker: IBKRBroker):
        self.active_oco_pairs: dict[str, tuple[int, int]] = {}  # group_id -> (order_id_a, order_id_b)

    async def place_oco_order(self, request: OCOOrderRequest) -> str:
        """Place both orders and link them."""

    async def _on_execution(self, order_id: int) -> None:
        """If one order fills, cancel the other."""
```

**Use Cases:**
- Enter long OR short (bracket around consolidation)
- Take profit at target OR stop loss (alternative to bracket)
- Scale out at multiple levels (partial OCO groups)

**Implementation Tasks:**
1. Add `OCOOrderRequest` to `models.py`
2. Create `OCOOrderManager` in `ibkr_trader/oco_orders.py`
3. Subscribe to `EXECUTION` events for order fills
4. Implement automatic cancellation of unfilled order
5. Handle partial fills (pro-rata cancellation)
6. CLI command: `ibkr-trader oco-order --symbol AAPL --buy-limit 145 --sell-limit 155 --quantity 10`

**Risk Considerations:**
- Race condition: both orders fill before cancellation (IBKR uses exchange OCO when possible)
- Network latency: delayed cancellation in fast markets
- Partial fills: adjust remaining quantity appropriately

**Tests Required:**
- `test_oco_order_cancellation()` - Unfilled order cancelled
- `test_oco_order_both_fill()` - Handle race condition gracefully
- `test_oco_order_partial_fill()` - Quantity adjustment
- `test_oco_order_broker_disconnect()` - Handle connection loss

**Documentation:**
- Add OCO order section to strategy guide
- Document race condition behavior
- Add examples for common use cases

**Tech Debt Prevention:**
- ✅ Handle race conditions explicitly
- ✅ Test connection loss scenarios
- ✅ Clear error messages for edge cases

---

### Step 4: Per-Symbol Position Limits (Session 2, Part 2)

**Goal:** Configurable limits per symbol (not just global max position size)

**Technical Design:**

```python
@dataclass
class SymbolLimits:
    """Per-symbol risk limits."""

    symbol: str
    max_position_size: int  # Max shares (overrides global limit)
    max_order_exposure: Decimal  # Max $ per order
    max_daily_loss: Decimal  # Max loss per day for this symbol
    max_correlation_exposure: Decimal | None  # Max exposure to correlated symbols

class SymbolLimitRegistry:
    """Registry of per-symbol limits with configuration loading."""

    def __init__(self, config_path: Path):
        self.limits: dict[str, SymbolLimits] = self._load_limits(config_path)

    def get_limit(self, symbol: str) -> SymbolLimits:
        """Get limits for symbol, falling back to defaults."""

    def validate_order(self, symbol: str, order: OrderRequest) -> None:
        """Check order against symbol-specific limits."""
```

**Configuration Format:**

```json
{
  "symbol_limits": {
    "AAPL": {
      "max_position_size": 50,
      "max_order_exposure": 5000.0,
      "max_daily_loss": 200.0
    },
    "TSLA": {
      "max_position_size": 10,
      "max_order_exposure": 2000.0,
      "max_daily_loss": 100.0,
      "max_correlation_exposure": 10000.0
    }
  },
  "default_limits": {
    "max_position_size": 100,
    "max_order_exposure": 10000.0,
    "max_daily_loss": 1000.0
  }
}
```

**Implementation Tasks:**
1. Add `SymbolLimits` and `SymbolLimitRegistry` to `ibkr_trader/risk.py`
2. Extend `RiskGuard` to use per-symbol limits
3. Add configuration loading from `data/symbol_limits.json`
4. Update dashboard to show per-symbol limit utilization
5. CLI command: `ibkr-trader set-symbol-limit --symbol AAPL --max-position 50 --max-loss 200`

**Risk Considerations:**
- Ensure per-symbol limits are stricter than global limits
- Handle symbol renames/delistings gracefully
- Persist limit changes across restarts

**Tests Required:**
- `test_symbol_limits_override_global()` - Per-symbol limits take precedence
- `test_symbol_limits_fallback_to_default()` - Unknown symbols use defaults
- `test_symbol_limits_validation()` - Order rejection when limits exceeded
- `test_symbol_limits_persistence()` - Configuration survives restart

**Documentation:**
- Add symbol limits section to risk management guide
- Document configuration file format
- Add examples for volatile vs stable stocks

**Tech Debt Prevention:**
- ✅ Configuration file with schema validation
- ✅ Clear precedence rules (symbol > default > global)
- ✅ Dashboard integration for visibility

---

### Step 5: Correlation-Based Portfolio Risk (Session 3, Part 1)

**Goal:** Limit total exposure to correlated symbols (prevent concentration risk)

**Technical Design:**

```python
class CorrelationMatrix:
    """Precomputed correlation matrix for portfolio risk."""

    def __init__(self, symbols: list[str], lookback_days: int = 60):
        self.correlations: dict[tuple[str, str], float] = {}

    async def update_correlations(self, market_data_client: MarketDataClient) -> None:
        """Fetch recent prices and recalculate correlations."""

    def get_correlation(self, symbol_a: str, symbol_b: str) -> float:
        """Get correlation coefficient between two symbols."""

    def get_correlated_symbols(self, symbol: str, threshold: float = 0.7) -> list[str]:
        """Get all symbols correlated above threshold."""

class CorrelationRiskGuard:
    """Risk guard based on portfolio correlation."""

    def __init__(self, correlation_matrix: CorrelationMatrix, max_correlated_exposure: Decimal):
        self.correlations = correlation_matrix
        self.max_exposure = max_correlated_exposure

    async def validate_order(
        self,
        symbol: str,
        order: OrderRequest,
        current_positions: dict[str, int],
    ) -> None:
        """Check if order would exceed correlated exposure limit."""
        # Get current exposure to correlated symbols
        # Calculate proposed new exposure
        # Reject if exceeds max_correlated_exposure
```

**Implementation Tasks:**
1. Add `CorrelationMatrix` and `CorrelationRiskGuard` to `ibkr_trader/risk.py`
2. Integrate with `RiskGuard` validation pipeline
3. Add correlation matrix caching (update daily, not per-order)
4. Display correlation warnings in dashboard
5. CLI command: `ibkr-trader update-correlations --symbols AAPL,MSFT,GOOGL,META --lookback 60`

**Risk Considerations:**
- Correlations are historical, may change rapidly in crisis
- Expensive to compute, must cache aggressively
- Consider sector/industry groupings (AAPL+MSFT are tech)

**Tests Required:**
- `test_correlation_calculation()` - Verify correlation math
- `test_correlation_risk_rejection()` - Order rejected when correlated exposure exceeded
- `test_correlation_cache_invalidation()` - Cache updates appropriately
- `test_correlation_sector_exposure()` - Sector-level grouping works

**Documentation:**
- Add correlation risk section to risk management guide
- Document caching behavior and update frequency
- Add examples with sector concentration

**Tech Debt Prevention:**
- ✅ Correlation matrix cached with TTL
- ✅ Graceful degradation if correlation data unavailable
- ✅ Performance testing with large portfolios

---

### Step 6: Order Management in Dashboard (Session 3, Part 2)

**Goal:** View, modify, and cancel orders from real-time dashboard

**Technical Design:**

```python
class OrderManagementPanel:
    """Interactive panel for managing active orders."""

    def __init__(self, broker: IBKRBroker, event_bus: EventBus):
        self.active_orders: dict[int, Order] = {}

    def render(self) -> Table:
        """Render active orders table."""
        table = Table(title="Active Orders")
        table.add_column("ID", style="cyan")
        table.add_column("Symbol", style="green")
        table.add_column("Side", style="yellow")
        table.add_column("Qty", justify="right")
        table.add_column("Type", style="magenta")
        table.add_column("Status", style="bold")
        table.add_column("Actions", style="red")

        for order_id, order in self.active_orders.items():
            table.add_row(
                str(order_id),
                order.contract.symbol,
                order.side.value,
                str(order.quantity),
                order.order_type.value,
                order.status,
                "[C] Cancel | [M] Modify"
            )
        return table

    async def handle_key_press(self, key: str) -> None:
        """Handle keyboard shortcuts for order management."""
        # 'c' + order ID: Cancel order
        # 'm' + order ID: Modify order
        # 'a': Cancel all orders
```

**Implementation Tasks:**
1. Add `OrderManagementPanel` to `dashboard.py`
2. Implement keyboard input handling (requires prompt_toolkit or similar)
3. Add order modification dialog (new price/quantity)
4. Add "Cancel All" confirmation dialog
5. Show order modification history in activity feed

**Risk Considerations:**
- Require confirmation for "Cancel All" action
- Disable order management during market close
- Log all manual interventions to telemetry

**Tests Required:**
- `test_dashboard_cancel_order()` - Order cancellation
- `test_dashboard_modify_order()` - Order modification
- `test_dashboard_cancel_all_confirmation()` - Confirmation dialog
- `test_dashboard_order_history()` - Activity feed tracking

**Documentation:**
- Add order management section to monitoring guide
- Document keyboard shortcuts
- Add screenshots/examples of order management workflow

**Tech Debt Prevention:**
- ✅ Keyboard handling with prompt_toolkit (well-tested library)
- ✅ All actions logged to telemetry
- ✅ Confirmation dialogs prevent accidents

---

## Testing Strategy

### Unit Tests
- Each new feature has dedicated test file
- Mock IBKR broker for fast tests
- Target: 100% coverage of new code

### Integration Tests
- Test with `SimulatedBroker` first
- Paper trading validation (manual testing)
- Live trading validation (small positions, manual oversight)

### Edge Case Tests
- Network disconnections during order placement
- Partial fills with bracket/OCO orders
- Race conditions (both OCO orders fill)
- Extreme volatility (trailing stops)

### Performance Tests
- Correlation matrix calculation with 100+ symbols
- Dashboard refresh rate with 50+ active orders
- Trailing stop updates at 1Hz for 20 symbols

---

## Documentation Plan

### User-Facing Documentation
1. **Advanced Order Types Guide** (new file: `docs/advanced_orders.md`)
   - Bracket orders with examples
   - Trailing stops with different configurations
   - OCO orders with use cases

2. **Risk Management Guide** (new file: `docs/risk_management.md`)
   - Per-symbol limits configuration
   - Correlation-based risk explanation
   - Portfolio-level risk controls

3. **Update Existing Docs**
   - `docs/monitoring_guide.md` - Add order management section
   - `docs/strategy_guide.md` - Add advanced order examples
   - `examples/README.md` - Add bracket order workflow

### Developer Documentation
- Architecture section in `CLAUDE.md` for order state machine
- API reference for new classes (docstrings)
- Testing guide for order type validation

---

## Tech Debt Prevention Checklist

For each step, ensure:

- ✅ Tests written alongside implementation (not after)
- ✅ Type hints with mypy strict mode passing
- ✅ Documentation updated immediately
- ✅ Error handling with clear messages
- ✅ Telemetry logging for debugging
- ✅ Configuration validated with Pydantic
- ✅ State persistence for restart resilience
- ✅ Performance tested with realistic workloads
- ✅ Edge cases explicitly handled
- ✅ Code reviewed against safety philosophy

---

## Risk Mitigation

### Before Each Step
1. Review existing code for integration points
2. Identify potential race conditions
3. Plan rollback strategy if issues arise

### During Implementation
1. Test with paper trading extensively
2. Start with small position sizes in live
3. Monitor telemetry for unexpected behavior

### After Each Step
1. Run full test suite (must pass 100%)
2. Lint and type check (must pass clean)
3. Manual testing in paper mode
4. Update documentation before moving on

---

## Success Criteria

### Phase 2 Complete When:
1. ✅ All 6 steps implemented and tested
2. ✅ 100% test coverage of new code
3. ✅ All documentation updated
4. ✅ Zero mypy/ruff errors
5. ✅ Paper trading validation successful
6. ✅ No new tech debt introduced

### User Value Delivered:
- Safer trading with automatic stop losses (bracket orders)
- Better profit capture with trailing stops
- More flexible order strategies (OCO orders)
- Granular risk control (per-symbol limits)
- Portfolio-level risk management (correlation)
- Real-time order control (dashboard management)

---

## Timeline

**Session 1:** Steps 1-2 (Bracket orders + Trailing stops)
**Session 2:** Steps 3-4 (OCO orders + Per-symbol limits)
**Session 3:** Steps 5-6 (Correlation risk + Dashboard order management)

**Total:** 3 sessions, zero tech debt
