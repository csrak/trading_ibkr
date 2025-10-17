#!/usr/bin/env bash
# Run backtest with trained model or SMA strategy
#
# This script demonstrates backtesting workflows:
# - Simple strategies (SMA, mean reversion)
# - ML-based strategies (industry model)
# - Config-based strategies

set -euo pipefail

# Choose strategy type
STRATEGY_TYPE="${1:-sma}"  # sma, industry, or config

echo "========================================="
echo "Running Backtest: $STRATEGY_TYPE"
echo "========================================="
echo ""

case "$STRATEGY_TYPE" in
    sma)
        echo "Testing Simple Moving Average strategy..."
        ibkr-trader backtest data/AAPL_2024.csv \
            --strategy sma \
            --fast 10 \
            --slow 20 \
            --size 10
        ;;

    industry)
        echo "Testing Industry Model strategy..."
        MODEL_PATH="model/artifacts/aapl_model/AAPL_linear_model.json"

        if [[ ! -f "$MODEL_PATH" ]]; then
            echo "ERROR: Model not found at $MODEL_PATH"
            echo "Run ./examples/scripts/01_train_model.sh first"
            exit 1
        fi

        ibkr-trader backtest data/AAPL_2024.csv \
            --strategy industry \
            --model-artifact "$MODEL_PATH" \
            --entry-threshold 0.01
        ;;

    config)
        echo "Testing config-based strategy..."
        CONFIG_PATH="${2:-examples/configs/mean_reversion.json}"

        if [[ ! -f "$CONFIG_PATH" ]]; then
            echo "ERROR: Config not found at $CONFIG_PATH"
            exit 1
        fi

        # Note: Config-based backtest uses replay mode
        python -c "
from pathlib import Path
from ibkr_trader.strategy_configs.config import load_strategy_config
from ibkr_trader.strategy_configs.factory import StrategyFactory

config = load_strategy_config(Path('$CONFIG_PATH'))
strategy = StrategyFactory.create(config)
print(f'Loaded strategy: {config.name}')
# TODO: Implement backtest runner for config-based strategies
print('Config-based backtest coming in Phase 2')
"
        ;;

    *)
        echo "ERROR: Unknown strategy type: $STRATEGY_TYPE"
        echo "Usage: $0 [sma|industry|config] [config_path]"
        exit 1
        ;;
esac

echo ""
echo "========================================="
echo "Backtest Complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "  1. Review results above"
echo "  2. Adjust parameters if needed"
echo "  3. Paper trade: ./examples/scripts/03_paper_trade.sh"
