# Documentation Index

Complete documentation for IBKR Personal Trader.

---

## Getting Started

- **[Quick Start Guide](../QUICKSTART.md)** - Installation, setup, and first trades
- **[Repository Guide](../CLAUDE.md)** - Development commands and architecture overview

---

## User Guides

### Trading & Execution
- **[Monitoring Guide](monitoring_guide.md)** ⭐ NEW - Real-time dashboard, session summaries, graceful shutdown
- **[Strategy Guide](unified_strategy_guide.md)** - Complete strategy development reference
- **[Strategy Quick Start](strategy_quick_start.md)** - Templates and examples
- **[Examples](../examples/README.md)** - Ready-to-run strategies and workflows

### Data & Models
- **[Model Training Guide](model_training_guide.md)** - ML model workflows and data caching
- **[Order Book Implementation](order_book_implementation.md)** - L2 market depth integration

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
2. [Monitoring Guide](monitoring_guide.md) - Learn the dashboard
3. [Examples](../examples/README.md) - Run example strategies

### For Developers
Start here if you're writing custom strategies:
1. [Repository Guide](../CLAUDE.md) - Architecture overview
2. [Strategy Guide](unified_strategy_guide.md) - Strategy API
3. [Strategy Quick Start](strategy_quick_start.md) - Templates

### For Quants
Start here if you're training ML models:
1. [Model Training Guide](model_training_guide.md)
2. [Order Book Implementation](order_book_implementation.md)
3. [Examples](../examples/README.md) - Industry model example

---

## Recent Updates

### Phase 1.5 - Monitoring & Observability (2025-10-17)

**New Features:**
- ✅ Real-time trading dashboard with live P&L
- ✅ Graceful shutdown with position warnings
- ✅ Enhanced session summaries with trade metrics
- ✅ Win rate and avg P&L per trade calculations

**Documentation Added:**
- [Monitoring Guide](monitoring_guide.md) - Complete monitoring reference

### Phase 1 - Unified Strategy Interface (2025-10-17)

**Features:**
- ✅ BaseStrategy with optional callbacks
- ✅ BrokerProtocol for context-agnostic strategies
- ✅ Config-based strategy loading
- ✅ Examples module with workflows

**Documentation Added:**
- [Strategy Guide](unified_strategy_guide.md)
- [Strategy Quick Start](strategy_quick_start.md)
- [Order Book Implementation](order_book_implementation.md)
- [Examples README](../examples/README.md)

---

## Support

**Questions or Issues:**
1. Check relevant guide above
2. Review [CLAUDE.md](../CLAUDE.md) for development guidance
3. Test with SimulatedBroker first
4. Review example implementations

**Remember:** This is real-money trading software. Always test thoroughly in paper trading before going live.
