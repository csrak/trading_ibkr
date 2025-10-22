"""Kill Switch Runbook"""

# Kill Switch Runbook

When the kill switch engages (CRITICAL alert or manual trigger), follow these steps before resuming trading:

1. **Acknowledge the alert**
   - Review alert context (session id, symbol) via `ibkr-trader monitoring alert-history --limit 10`
   - Confirm no new alerts continue to stream: `ibkr-trader monitoring alert-history --follow`

2. **Verify order cancellations**
   - `ibkr-trader monitoring kill-switch-status`
   - Check broker logs for `kill_switch.cancel_failed` diagnostics. If this appears, manually cancel outstanding orders in TWS and rerun status.

3. **Confirm screener recovery**
   - Ensure a fresh `*.screen_refresh` alert has been logged after the CRITICAL event.

4. **Clear the kill switch**
   - `ibkr-trader monitoring kill-switch-clear --note "<incident summary>"`

5. **Resume trading**
   - Restart strategy (`ibkr-trader run ...`). The system will refuse to start if the kill switch is still engaged.
