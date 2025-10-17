# CLI Refactoring Plan

## Problem

`ibkr_trader/cli.py` is currently **1593 lines**, making it difficult to:
- Navigate and maintain
- Add new commands efficiently
- Test individual command groups in isolation
- Understand responsibilities at a glance

## Current Structure Analysis

The file contains:
- **Utility functions** (setup_logging, create_market_data_client, formatters, loaders)
- **Trading commands** (run, paper-order, paper-quick, status)
- **Data commands** (backtest, train-model, cache-option-chain)
- **Monitoring commands** (diagnostics, session-status, monitor-telemetry, dashboard)
- **Async helper functions** (run_strategy, check_status, submit_single_order, etc.)

## Proposed Structure

```
ibkr_trader/
├── cli.py                    # Main entry point (~100 lines)
│   └── app = typer.Typer()
│       └── Registers command groups
│
├── cli_commands/             # New directory
│   ├── __init__.py
│   │
│   ├── trading.py           # Trading commands (~400 lines)
│   │   ├── @trading_app.command() status
│   │   ├── @trading_app.command() paper-order
│   │   ├── @trading_app.command() paper-quick
│   │   ├── @trading_app.command() bracket-order  ← NEW
│   │   ├── @trading_app.command() run
│   │   └── Helper: submit_single_order()
│   │
│   ├── monitoring.py        # Monitoring commands (~300 lines)
│   │   ├── @monitoring_app.command() diagnostics
│   │   ├── @monitoring_app.command() session-status
│   │   ├── @monitoring_app.command() monitor-telemetry
│   │   └── @monitoring_app.command() dashboard
│   │
│   ├── data.py              # Data/model commands (~400 lines)
│   │   ├── @data_app.command() backtest
│   │   ├── @data_app.command() train-model
│   │   └── @data_app.command() cache-option-chain
│   │
│   └── utils.py             # Shared utilities (~200 lines)
│       ├── setup_logging()
│       ├── create_market_data_client()
│       ├── create_option_chain_client()
│       ├── _format_seconds()
│       ├── _format_telemetry_line()
│       ├── _load_portfolio_snapshot()
│       └── _tail_telemetry_entries()
│
└── tests/
    ├── test_cli.py                    # Existing integration tests
    └── test_cli_commands/             # New unit tests
        ├── test_trading.py
        ├── test_monitoring.py
        └── test_data.py
```

## Implementation Steps

### Phase 1: Create Structure (No Breaking Changes)

1. **Create `cli_commands/` directory**
   ```bash
   mkdir -p ibkr_trader/cli_commands
   touch ibkr_trader/cli_commands/__init__.py
   ```

2. **Extract utilities to `cli_commands/utils.py`**
   - Move all helper functions (setup_logging, formatters, loaders)
   - Keep imports minimal and focused

3. **Extract trading commands to `cli_commands/trading.py`**
   - Create `trading_app = typer.Typer()`
   - Move: status, paper-order, paper-quick, run
   - Move async helpers: run_strategy, check_status, submit_single_order
   - Add **bracket-order command here** (new)

4. **Extract monitoring commands to `cli_commands/monitoring.py`**
   - Create `monitoring_app = typer.Typer()`
   - Move: diagnostics, session-status, monitor-telemetry, dashboard
   - Move async helpers: run_dashboard, etc.

5. **Extract data commands to `cli_commands/data.py`**
   - Create `data_app = typer.Typer()`
   - Move: backtest, train-model, cache-option-chain

6. **Simplify main `cli.py`**
   ```python
   import typer
   from ibkr_trader.cli_commands.trading import trading_app
   from ibkr_trader.cli_commands.monitoring import monitoring_app
   from ibkr_trader.cli_commands.data import data_app

   app = typer.Typer(
       name="ibkr-trader",
       help="IBKR Personal Trader CLI",
   )

   # Register command groups
   app.add_typer(trading_app, name="trade", help="Trading commands")
   app.add_typer(monitoring_app, name="monitor", help="Monitoring commands")
   app.add_typer(data_app, name="data", help="Data and model commands")

   # Backward compatibility: also register at root level
   for command in trading_app.registered_commands:
       app.command()(command.callback)
   # ... repeat for other groups

   if __name__ == "__main__":
       app()
   ```

### Phase 2: Test and Validate

1. **Run existing tests**
   ```bash
   pytest tests/test_cli.py -v
   ```

2. **Test CLI invocation**
   ```bash
   ibkr-trader status
   ibkr-trader paper-order --help
   ibkr-trader backtest --help
   ```

3. **Add new bracket-order command**
   - Should be easier now in `cli_commands/trading.py`
   - Follow existing pattern from `paper-order`

4. **Update documentation**
   - Add note to README about command organization
   - Update QUICKSTART if needed

### Phase 3: Cleanup (Optional)

1. **Consider grouped command syntax** (breaking change if adopted exclusively):
   ```bash
   # Current (preserved for backward compatibility)
   ibkr-trader status
   ibkr-trader paper-order --symbol AAPL

   # New grouped syntax (optional addition)
   ibkr-trader trade status
   ibkr-trader trade paper-order --symbol AAPL
   ibkr-trader monitor diagnostics
   ibkr-trader data backtest data/AAPL_1d.csv
   ```

2. **Add unit tests for command groups**
   - Create `tests/test_cli_commands/` directory
   - Test each command group independently

## Backward Compatibility Strategy

**Critical**: We must maintain backward compatibility for all existing CLI invocations.

**Solution**: Register commands at both root level AND in groups:
- Root level: `ibkr-trader status` (existing behavior)
- Grouped: `ibkr-trader trade status` (new option)

This allows gradual migration without breaking existing scripts or documentation.

## Benefits

1. **Maintainability**: Each file is 200-400 lines instead of 1593
2. **Clarity**: Related commands grouped together
3. **Testability**: Can test command groups independently
4. **Extensibility**: Easy to add new commands in appropriate files
5. **Zero Breaking Changes**: All existing commands work exactly as before

## File Size Comparison

| File | Before | After |
|------|--------|-------|
| `cli.py` | 1593 lines | ~100 lines |
| `cli_commands/trading.py` | - | ~400 lines |
| `cli_commands/monitoring.py` | - | ~300 lines |
| `cli_commands/data.py` | - | ~400 lines |
| `cli_commands/utils.py` | - | ~200 lines |

## Timeline

- **Step 1-2** (Create structure + Extract utils): 15 minutes
- **Step 3** (Extract trading commands + add bracket-order): 30 minutes
- **Step 4-5** (Extract other command groups): 20 minutes
- **Step 6** (Simplify main cli.py): 10 minutes
- **Testing & Validation**: 15 minutes

**Total estimated time**: ~90 minutes

## Next Steps

1. Get approval on this plan
2. Create the directory structure
3. Extract utilities first (lowest risk)
4. Extract trading commands and add bracket-order
5. Extract remaining command groups
6. Test thoroughly
7. Complete Step 1 documentation (bracket orders in strategy guide)
8. Move to Step 2 (Trailing Stops)
