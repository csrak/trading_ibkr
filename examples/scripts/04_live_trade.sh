#!/usr/bin/env bash
# Run strategy in LIVE TRADING mode
#
# ⚠️  WARNING: REAL MONEY AT RISK ⚠️
# This script trades with real capital in your IBKR live account
#
# Prerequisites:
# 1. Thoroughly backtested strategy (6+ months data)
# 2. Paper traded successfully (2+ weeks)
# 3. Reviewed ALL trades manually
# 4. Set strict risk limits in .env
# 5. Start with SMALL position sizes

set -euo pipefail

# Strategy configuration
STRATEGY_TYPE="${1:-}"
CONFIG_PATH="${2:-}"

echo "========================================="
echo "⚠️  LIVE TRADING WARNING ⚠️"
echo "========================================="
echo ""
echo "This script will trade with REAL MONEY in your IBKR account."
echo ""
echo "Have you completed ALL of the following?"
echo "  [ ] Backtested strategy for 6+ months"
echo "  [ ] Paper traded successfully for 2+ weeks"
echo "  [ ] Reviewed ALL paper trades manually"
echo "  [ ] Set risk limits in .env (MAX_DAILY_LOSS, MAX_ORDER_EXPOSURE)"
echo "  [ ] Starting with SMALL position sizes"
echo "  [ ] Have a kill switch plan"
echo ""

read -p "Type 'I UNDERSTAND THE RISKS' to continue: " -r
if [[ "$REPLY" != "I UNDERSTAND THE RISKS" ]]; then
    echo "Aborted."
    exit 1
fi

echo ""
echo "========================================="
echo "Live Trading Setup"
echo "========================================="
echo ""

# Check environment
if [[ "${IBKR_TRADING_MODE:-}" != "live" ]]; then
    echo "ERROR: IBKR_TRADING_MODE must be set to 'live' in .env"
    echo "Add this line to your .env file:"
    echo "  IBKR_TRADING_MODE=live"
    exit 1
fi

# Check IBKR connection (live port is 7496)
echo "Checking IBKR live connection..."
if ! nc -z localhost 7496 2>/dev/null; then
    echo "ERROR: IBKR Gateway/TWS not running on port 7496 (live)"
    echo ""
    echo "Please start IBKR Gateway or TWS in LIVE mode:"
    echo "  1. Launch IB Gateway or TWS"
    echo "  2. Login with LIVE credentials"
    echo "  3. Enable API in settings (port 7496)"
    echo "  4. Re-run this script"
    exit 1
fi
echo "✓ IBKR live connection available"
echo ""

# Validate strategy
if [[ -z "$STRATEGY_TYPE" ]]; then
    echo "ERROR: Strategy type required"
    echo "Usage: $0 [sma|config] [config_path]"
    exit 1
fi

# Final confirmation
echo "⚠️  FINAL CONFIRMATION ⚠️"
echo "Strategy: $STRATEGY_TYPE"
if [[ -n "$CONFIG_PATH" ]]; then
    echo "Config: $CONFIG_PATH"
fi
echo ""
read -p "Start LIVE trading? (yes/no): " -r
if [[ "$REPLY" != "yes" ]]; then
    echo "Aborted."
    exit 1
fi

echo ""
echo "Starting LIVE trading..."
echo "Press Ctrl+C to stop (will close positions gracefully)"
echo ""

case "$STRATEGY_TYPE" in
    sma)
        ibkr-trader run \
            --symbol AAPL \
            --fast 10 \
            --slow 20 \
            --size 10 \
            --live
        ;;

    config)
        if [[ -z "$CONFIG_PATH" ]] || [[ ! -f "$CONFIG_PATH" ]]; then
            echo "ERROR: Valid config path required for config strategy"
            exit 1
        fi

        ibkr-trader run --config "$CONFIG_PATH" --live
        ;;

    *)
        echo "ERROR: Unknown strategy type: $STRATEGY_TYPE"
        echo "Usage: $0 [sma|config] [config_path]"
        exit 1
        ;;
esac

echo ""
echo "========================================="
echo "Live Trading Session Ended"
echo "========================================="
echo ""
echo "IMPORTANT:"
echo "  1. Check all positions are closed"
echo "  2. Review P&L in IBKR account"
echo "  3. Save logs for tax reporting"
echo "  4. Monitor for any errors"
