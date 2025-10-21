# Documentation Index

Complete documentation for IBKR Personal Trader.

---

## Getting Started

- **[Quick Start Guide](../QUICKSTART.md)** - Installation, setup, and first trades
- **[Repository Guide](../CLAUDE.md)** - Development commands and architecture overview

---

## Guides

### Trading & Execution
- **[Monitoring Guide](guides/operations/monitoring_guide.md)** ⭐ NEW - Real-time dashboard, session summaries, graceful shutdown
- **[Risk Management Guide](guides/operations/risk_management.md)** - Per-symbol limits, correlation guards, safety checklists
- **[Bracket Orders Guide](guides/trading/bracket_orders_guide.md)** - Entry + stop + take-profit workflows
- **[Trailing Stops Guide](guides/trading/trailing_stops_guide.md)** - Dynamic stop management and rate limits

### Strategy Development
- **[Strategy Guide](guides/strategies/unified_strategy_guide.md)** - Complete strategy development reference
- **[Strategy Quick Start](guides/strategies/strategy_quick_start.md)** - Templates and examples
- **[Adaptive Momentum Plan](plans/active/adaptive_momentum_plan.md)** - Current next-gen strategy roadmap

### Data & Modeling
- **[Model Training Guide](guides/data/model_training_guide.md)** - ML model workflows and data caching
- **[Order Book Implementation](architecture/order_book_implementation.md)** - L2 market depth integration
- **[Strategy Coordinator Architecture](architecture/strategy_coordinator_architecture.md)** - Coordinator design notes

### Market Making Research
- **[Market Making Architecture](market_making/architecture.md)** - Design for depth-driven strategies
- **[Market Making Data Schemas](market_making/data_schemas.md)** - L2 data requirements
- **[Market Making Simulation Plan](market_making/simulation_plan.md)** - Replay and stress scenarios

### Examples
- **[Examples](../examples/README.md)** - Ready-to-run strategies and workflows

---

## Quick Reference

### Commands

```bash
# Trading
ibkr-trader run --symbol AAPL              # Run strategy
ibkr-trader dashboard                       # Real-time monitoring ⭐ NEW
ibkr-trader status                          # Check connection

# Analysis
ibkr-trader session-status                  # View session summary ⭐ ENHANCED
ibkr-trader backtest data.csv               # Run backtest
ibkr-trader monitor-telemetry --follow      # Watch logs

# Training
ibkr-trader train-model --target AAPL --peer MSFT  # Train ML model
ibkr-trader diagnostics                     # Check cache/limits
```

### Key Features

1. **Real-Time Dashboard** (`ibkr-trader dashboard`)
   - Live P&L tracking
   - Position monitoring with warnings
   - Risk indicators
   - Recent activity feed

2. **Graceful Shutdown** (Ctrl+C during trading)
   - Final P&L summary
   - Open position alerts
   - Per-symbol breakdown

3. **Enhanced Summaries** (`ibkr-trader session-status`)
   - Win rate metrics
   - Trade statistics
   - Recommended actions

---

## Documentation by Role

### For Traders
Start here if you're using the platform to trade:
1. [Quick Start Guide](../QUICKSTART.md)
2. [Monitoring Guide](guides/operations/monitoring_guide.md) - Learn the dashboard
3. [Examples](../examples/README.md) - Run example strategies

### For Developers
Start here if you're writing custom strategies:
1. [Repository Guide](../CLAUDE.md) - Architecture overview
2. [Strategy Guide](guides/strategies/unified_strategy_guide.md) - Strategy API
3. [Strategy Quick Start](guides/strategies/strategy_quick_start.md) - Templates

### For Quants
Start here if you're training ML models:
1. [Model Training Guide](guides/data/model_training_guide.md)
2. [Order Book Implementation](architecture/order_book_implementation.md)
3. [Examples](../examples/README.md) - Industry model example

---

## Plans & Status

- **[Platform Progress & Roadmap](status/platform_progress.md)** - Phase status and next-phase options
- **Active Plans**
  - [Phase 2: Advanced Order Types & Risk Management](plans/active/advanced_order_types_risk_plan.md)
  - [Adaptive Momentum Strategy Plan](plans/active/adaptive_momentum_plan.md)
- **Completed Initiatives**
  - [Phase 1: Unified Strategy Interface](plans/completed/phase1_unified_strategy_progress.md)
  - [CLI Refactor Plan (Historical Notes)](plans/completed/cli_refactor_plan.md)

---

## Recent Updates

### Phase 1.5 - Monitoring & Observability (2025-10-17)

**New Features:**
- ✅ Real-time trading dashboard with live P&L
- ✅ Graceful shutdown with position warnings
- ✅ Enhanced session summaries with trade metrics
- ✅ Win rate and avg P&L per trade calculations

**Documentation Added:**
- [Monitoring Guide](guides/operations/monitoring_guide.md) - Complete monitoring reference

### Phase 1 - Unified Strategy Interface (2025-10-17)

**Features:**
- ✅ BaseStrategy with optional callbacks
- ✅ BrokerProtocol for context-agnostic strategies
- ✅ Config-based strategy loading
- ✅ Examples module with workflows

**Documentation Added:**
- [Strategy Guide](guides/strategies/unified_strategy_guide.md)
- [Strategy Quick Start](guides/strategies/strategy_quick_start.md)
- [Order Book Implementation](architecture/order_book_implementation.md)
- [Examples README](../examples/README.md)

---

## Support

**Questions or Issues:**
1. Check relevant guide above
2. Review [CLAUDE.md](../CLAUDE.md) for development guidance
3. Test with SimulatedBroker first
4. Review example implementations

**Remember:** This is real-money trading software. Always test thoroughly in paper trading before going live.
