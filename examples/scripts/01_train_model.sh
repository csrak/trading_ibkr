#!/usr/bin/env bash
# Train industry model with peer stocks
#
# This script demonstrates the complete model training workflow:
# 1. Download historical data (yfinance or IBKR)
# 2. Train linear regression model on peer returns
# 3. Generate predictions and save artifacts
# 4. Create visualization plots

set -euo pipefail

# Configuration
TARGET_SYMBOL="AAPL"
PEER_SYMBOLS=("MSFT" "GOOGL" "AMZN")
START_DATE="2023-01-01"
END_DATE="2024-01-01"
HORIZON_DAYS=5
ARTIFACT_DIR="model/artifacts/aapl_model"

echo "========================================="
echo "Training Industry Model"
echo "========================================="
echo "Target: $TARGET_SYMBOL"
echo "Peers: ${PEER_SYMBOLS[*]}"
echo "Date Range: $START_DATE to $END_DATE"
echo "Forecast Horizon: $HORIZON_DAYS days"
echo "Artifact Directory: $ARTIFACT_DIR"
echo ""

# Build peer arguments
PEER_ARGS=""
for peer in "${PEER_SYMBOLS[@]}"; do
    PEER_ARGS="$PEER_ARGS --peer $peer"
done

# Train model
echo "Training model..."
ibkr-trader train-model \
    --target "$TARGET_SYMBOL" \
    $PEER_ARGS \
    --start "$START_DATE" \
    --end "$END_DATE" \
    --horizon "$HORIZON_DAYS" \
    --artifact-dir "$ARTIFACT_DIR"

echo ""
echo "========================================="
echo "Training Complete!"
echo "========================================="
echo ""
echo "Generated artifacts:"
ls -lh "$ARTIFACT_DIR"
echo ""
echo "Next steps:"
echo "  1. Review plots: open $ARTIFACT_DIR/*.png"
echo "  2. Backtest: ./examples/scripts/02_backtest.sh"
echo "  3. Paper trade: ./examples/scripts/03_paper_trade.sh"
