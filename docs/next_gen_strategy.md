# Next-Generation Live Strategy Plan

**Date:** 2025-10-20  
**Owner:** Trading Platform Team  

## 1. Objectives

1. Deliver a production-ready paper/live strategy that can survive realistic fee and slippage regimes.
2. Automate symbol selection so the system scales beyond hand-picked tickers.
3. Keep safety guarantees (risk guards, telemetry, graceful shutdown) consistent with existing architecture.

## 2. Strategy Concept – Adaptive Momentum / Mean-Reversion Hybrid

| Component        | Description                                                                                           |
| ---------------- | ----------------------------------------------------------------------------------------------------- |
| Universe         | Top N symbols ranked by daily dollar volume and liquidity screen (see Section 4).                     |
| Signal Layer     | Dual overlay: (a) short-term trend strength (e.g. 5 vs 20 bar momentum), (b) intraday reversion using VWAP/ATR. |
| Position Sizing  | Volatility-adjusted target using recent ATR, capped by per-symbol + correlated exposure guards.       |
| Execution        | Market or discretionary limit with dynamic offset; skip trades when expected edge < fees + buffer.    |
| Risk Controls    | Existing `RiskGuard`, new correlation guard, configurable max drawdown per session, time-based flattening. |
| Monitoring       | Telemetry events: signal strength, chosen size, expected vs realized edge, reasons for skips.         |

### Trade Lifecycle
1. Screener produces ranked universe → coordinator registers desired symbols.
2. Strategy receives market data bars (1m) via `MarketDataService`.
3. Signal engine computes target delta; if edge > fee buffer and risk budget available, an intent is published.
4. Coordinator handles order routing, risk guard enforces notional limits, correlation guard protects clustered exposure.
5. Strategy logs outcome; telemetry summarises fills vs expected edge.

## 3. Implementation Outline

### 3.1 Modules
- `ibkr_trader/strategies/adaptive_momentum.py`: main strategy class.
- `ibkr_trader/strategies/config.py`: Pydantic config with parameters (lookbacks, thresholds, risk caps).
- `ibkr_trader/strategies/factors.py`: reusable factor calculators (momentum, mean reversion, volatility).
- `ibkr_trader/data/screeners/base.py`: interfaces for symbol screeners.
- `ibkr_trader/data/screeners/liquidity.py`: first implementation (volume/liquidity filters).
- `ibkr_trader/screeners/momentum.py`: optional ranking by intermediate signal.
- `ibkr_trader/strategy_configs/adaptive_momentum.json`: example config for immediate use.

### 3.2 Data Flow
```text
MarketDataService → AdaptiveMomentumStrategy → OrderIntent → StrategyCoordinator
                      ↑                                   ↓
            ScreenerService provides universe    RiskGuard + CorrelationGuard enforce limits
```

### 3.3 Risk Integrations
- Use existing `RiskGuard` for daily loss + per-symbol caps.
- Correlation guard already wired; strategy will emit sector tags to improve diagnostics.
- Add optional session kill-switch (stop trading if cumulative realized pnl < -X or total trades > Y).

### 3.4 Telemetry Additions
- `strategy.signal_snapshot`: {symbol, signal_strength, vol_estimate, expected_edge}
- `strategy.trade_skip`: {symbol, reason (fees, risk_budget, signal_below_threshold)}
- `strategy.screen_refresh`: {universe, timestamp, screener_name}

## 4. Screener Roadmap

1. **Liquidity Filter (MVP)**  
   - Pull average daily dollar volume from cached fundamentals or yfinance (existing training pipeline).  
   - Enforce min ADR, price > $5, exclude ETF blacklist.  
   - Output top N names per session, refresh every 15 minutes.

2. **Momentum Pre-Ranker**  
   - Daily/weekly momentum percentile to prioritise strongest trends.  
   - Provide tie-breakers for strategy to allocate capital.

3. **Event-Based Flags (Future)**  
   - Earnings calendar, news sentiment, abnormal volume alerts.

(We will keep expanding this roadmap as each piece ships—please treat this document as a living tracker.)

## 5. Incremental Delivery Plan

| Sprint | Scope                                                                                                 | Status    | Notes/Next Actions                                                         |
| ------ | ------------------------------------------------------------------------------------------------------ | --------- | --------------------------------------------------------------------------- |
| 1      | Strategy scaffolding, config, basic telemetry, liquidity screener placeholder                         | ✅ Done    | Delivered adaptive momentum skeleton and CLI wiring                        |
| 2      | Wire screener into runtime, integrate ATR/VWAP feeds, add fee-aware filters, enhanced telemetry       | 🚧 Ongoing | Next: build screener scheduler, hook edge calculations to real inputs       |
| 3      | Adaptive sizing, session kill switch, local runbook, backtest validation, expanded dashboard          | Planned   | To schedule once Sprint 2 stabilizes                                        |

## 6. Testing Plan

- Unit tests for factors, screener filters, sizing logic.
- Integration test with simulated broker ensuring intents respect caps.
- Paper trading dry-run with telemetry verification.

## 7. Documentation Tasks

- ✅ Added adaptive momentum CLI usage to `README.md` (Sprint 1)
- 🔄 Update `docs/strategy_quick_start.md` once screener scheduling lands (Sprint 2)
- 🔄 Document screener CLI/API and runbook snippets (Sprint 3)

---

**Next Steps:** Implement scaffolding (`AdaptiveMomentumStrategy`, screener interface) and wire into CLI via strategy graph example. Follow with unit tests and documentation updates.
