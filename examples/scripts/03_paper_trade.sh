#!/usr/bin/env bash
# Run strategy in paper trading mode
#
# This script connects to IBKR paper trading account and runs
# a strategy in real-time with live market data (no real money at risk)

set -euo pipefail

# Choose strategy
STRATEGY_TYPE="${1:-sma}"  # sma, config

echo "========================================="
echo "Paper Trading Setup"
echo "========================================="
echo ""

# Pre-flight checks
echo "Checking IBKR connection..."
if ! nc -z localhost 7497 2>/dev/null; then
    echo "ERROR: IBKR Gateway/TWS not running on port 7497"
    echo ""
    echo "Please start IBKR Gateway or TWS in paper trading mode:"
    echo "  1. Launch IB Gateway or TWS"
    echo "  2. Login with paper trading credentials"
    echo "  3. Enable API in settings (port 7497)"
    echo "  4. Re-run this script"
    exit 1
fi
echo "✓ IBKR connection available"
echo ""

# Check environment
if [[ "${IBKR_TRADING_MODE:-paper}" != "paper" ]]; then
    echo "WARNING: IBKR_TRADING_MODE is set to '${IBKR_TRADING_MODE}'"
    echo "For paper trading, ensure IBKR_TRADING_MODE=paper in .env"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "Starting paper trading..."
echo ""

case "$STRATEGY_TYPE" in
    sma)
        echo "Running SMA strategy (fast=10, slow=20)..."
        ibkr-trader run \
            --symbol AAPL \
            --fast 10 \
            --slow 20 \
            --size 10
        ;;

    config)
        CONFIG_PATH="${2:-examples/configs/mean_reversion.json}"

        if [[ ! -f "$CONFIG_PATH" ]]; then
            echo "ERROR: Config not found at $CONFIG_PATH"
            exit 1
        fi

        echo "Running config-based strategy: $CONFIG_PATH"
        ibkr-trader run --config "$CONFIG_PATH"
        ;;

    *)
        echo "ERROR: Unknown strategy type: $STRATEGY_TYPE"
        echo "Usage: $0 [sma|config] [config_path]"
        exit 1
        ;;
esac

echo ""
echo "========================================="
echo "Paper Trading Session Ended"
echo "========================================="
echo ""
echo "Next steps:"
echo "  1. Review trades in IBKR paper account"
echo "  2. Check logs for any errors"
echo "  3. Adjust parameters if needed"
echo "  4. When ready for live: ./examples/scripts/04_live_trade.sh"
