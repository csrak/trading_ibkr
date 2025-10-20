# Risk Management Guide

The IBKR Personal Trader platform layers multiple safeguards on every order.
This guide describes the controls that sit on top of the global limits defined
in your environment configuration.

## Per-Symbol Limits

Global limits (`IBKR_MAX_POSITION_SIZE`, `IBKR_MAX_ORDER_EXPOSURE`,
`IBKR_MAX_DAILY_LOSS`) apply to every instrument. When you need tighter
constraints for a particular symbol you can define them in
`data/symbol_limits.json`. The format supports optional defaults plus
symbol-specific overrides:

```json
{
  "default_limits": {
    "max_position_size": 25,
    "max_order_exposure": 7500.0,
    "max_daily_loss": 300.0
  },
  "symbol_limits": {
    "TSLA": {
      "max_position_size": 10,
      "max_order_exposure": 3000.0,
      "max_daily_loss": 150.0
    }
  }
}
```

- **Defaults** apply whenever no explicit symbol entry exists.
- **Symbol overrides** must be stricter (lower) than the global values to avoid
  accidentally raising limits.

### CLI Management

Use the CLI to create or update entries without editing JSON by hand:

```bash
# Tighten TSLA exposure and daily loss limits
ibkr-trader set-symbol-limit --symbol TSLA \
  --max-position 10 --max-exposure 3000 --max-loss 150

# Adjust fallback defaults applied to every other symbol
ibkr-trader set-symbol-limit --default --max-position 50 --max-loss 500
```

The command persists changes to `data/symbol_limits.json` and validates that the
requested limits never exceed your global configuration.

### Runtime Enforcement

- `RiskGuard` evaluates per-symbol limits before every order submission.
- Per-symbol daily loss checks use realised P&L captured from execution events;
  once the threshold is breached further orders for that symbol are rejected.
- The real-time dashboard now shows a *Limit Util* column (with `*` denoting
  symbol-specific overrides) so you can see position pressure at a glance.

### Tips

- Keep per-symbol limits conservative for volatile or low-liquidity names.
- Review limits alongside strategy deployments; store them in version control
  via the generated JSON file if you need an audit trail.
- Pair per-symbol limits with bracket orders or trailing stops to maintain
  consistent downside protection.

## Correlation-Based Exposure Limits

When multiple symbols move together, a portfolio can exceed intended risk even
if individual positions respect their limits. Enable the correlation guard to
cap combined exposure across highly correlated names.

### Configuration

1. Set environment variables (or `.env`) for:
   - `IBKR_MAX_CORRELATED_EXPOSURE`: Maximum combined notional exposure in USD.
   - `IBKR_CORRELATION_THRESHOLD`: Minimum absolute correlation (0‒1) before
     a symbol is considered related. Defaults to `0.75`.
2. Provide a correlation matrix in `data/correlation_matrix.json`:

```json
{
  "AAPL": {
    "MSFT": 0.86,
    "QQQ": 0.81
  },
  "MSFT": {
    "QQQ": 0.79
  }
}
```

Values are Pearson correlations in the range [-1, 1]. The loader is symmetric,
so you can list each pair once.

### Behaviour

- Every order recalculates projected exposure for the target symbol and adds
  the current exposure of all correlated symbols above the threshold.
- If the combined notional exceeds `IBKR_MAX_CORRELATED_EXPOSURE`, the order is
  rejected before it reaches IBKR.
- Closing or reducing a position *reduces* the combined exposure, allowing the
  guard to stay out of the way when you unwind risk.

### Maintaining the Matrix

- The file format is intentionally simple JSON—generate it from your analytics
  pipeline or export from a notebook.
- Store the matrix alongside your strategy configs so updates can be reviewed.
- Recompute correlations periodically; fast-changing markets can break stale
  assumptions.
